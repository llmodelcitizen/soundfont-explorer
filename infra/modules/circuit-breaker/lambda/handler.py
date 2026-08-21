"""Egress circuit breaker (plan §11).

Triggered (a) by the CloudWatch alarm "daily BytesDownloaded > DAILY_GB" via SNS and
(b) hourly by EventBridge, where it sums BytesDownloaded since the start of the month and
trips if it exceeds MONTHLY_GB. Tripping = CloudFront UpdateDistribution(Enabled=false) +
an SNS email. Re-enabling is manual (docs/DEPLOY.md).
"""
import datetime as dt
import json
import os

import boto3

DIST = os.environ["DISTRIBUTION_ID"]
TOPIC = os.environ["TOPIC_ARN"]
DAILY_GB = float(os.environ.get("DAILY_GB", "150"))
MONTHLY_GB = float(os.environ.get("MONTHLY_GB", "900"))
GB = 1024 ** 3

cf = boto3.client("cloudfront")
cw = boto3.client("cloudwatch", region_name="us-east-1")
sns = boto3.client("sns")


def month_to_date_bytes(now: dt.datetime) -> float:
    start = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    r = cw.get_metric_statistics(
        Namespace="AWS/CloudFront", MetricName="BytesDownloaded",
        Dimensions=[{"Name": "DistributionId", "Value": DIST}, {"Name": "Region", "Value": "Global"}],
        StartTime=start, EndTime=now, Period=86400, Statistics=["Sum"])
    return float(sum(p["Sum"] for p in r.get("Datapoints", [])))


def trip(reason: str) -> dict:
    cfg = cf.get_distribution_config(Id=DIST)
    dc, etag = cfg["DistributionConfig"], cfg["ETag"]
    already = not dc["Enabled"]
    if not already:
        dc["Enabled"] = False
        cf.update_distribution(Id=DIST, IfMatch=etag, DistributionConfig=dc)
    msg = (f"Soundfont Explorer circuit breaker {'(already disabled) ' if already else ''}tripped: {reason}\n\n"
           f"Distribution {DIST} is DISABLED. To re-enable after investigating, see docs/DEPLOY.md "
           f"(aws cloudfront update-distribution … Enabled=true).")
    sns.publish(TopicArn=TOPIC, Subject="[Soundfont Explorer] CloudFront disabled by circuit breaker", Message=msg)
    return {"tripped": True, "already_disabled": already, "reason": reason}


def handler(event, _ctx):
    now = dt.datetime.now(dt.timezone.utc)
    # (a) alarm via SNS
    for rec in event.get("Records", []):
        if rec.get("EventSource") == "aws:sns":
            try:
                body = json.loads(rec["Sns"]["Message"])
            except (ValueError, KeyError):
                body = {}
            if body.get("NewStateValue") == "ALARM":
                return trip(f"daily egress alarm: {body.get('AlarmName')} — {body.get('NewStateReason')}")
            return {"ignored": body.get("NewStateValue")}
    # (b) scheduled month-to-date check
    mtd = month_to_date_bytes(now)
    if mtd > MONTHLY_GB * GB:
        return trip(f"month-to-date egress {mtd / GB:.1f} GB > {MONTHLY_GB:.0f} GB")
    return {"tripped": False, "month_to_date_gb": round(mtd / GB, 2)}
