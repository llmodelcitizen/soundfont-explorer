"""Egress circuit breaker (plan §11).

Triggered (a) by the CloudWatch alarm "daily BytesDownloaded > DAILY_GB" via SNS and
(b) hourly by EventBridge, where it sums BytesDownloaded over the trailing 24 h and since the
start of the month and trips if either exceeds its threshold. The alarm only fires on the
OK→ALARM transition, so (b) is what re-trips a distribution that was re-enabled while still
over the daily threshold. Tripping = CloudFront UpdateDistribution(Enabled=false) + an SNS
email. Re-enabling is manual (docs/DEPLOY.md). A trip that cannot disable the distribution
mails the same topic and re-raises so the invocation fails (retried, then delivered to the
function's on-failure destination) instead of being dropped silently.
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


def bytes_downloaded(start: dt.datetime, end: dt.datetime, period: int) -> float:
    r = cw.get_metric_statistics(
        Namespace="AWS/CloudFront", MetricName="BytesDownloaded",
        Dimensions=[{"Name": "DistributionId", "Value": DIST}, {"Name": "Region", "Value": "Global"}],
        StartTime=start, EndTime=end, Period=period, Statistics=["Sum"])
    return float(sum(p["Sum"] for p in r.get("Datapoints", [])))


def month_to_date_bytes(now: dt.datetime) -> float:
    return bytes_downloaded(now.replace(day=1, hour=0, minute=0, second=0, microsecond=0), now, 86400)


def trailing_day_bytes(now: dt.datetime) -> float:
    # Hourly buckets summed: a true trailing window whatever CloudWatch aligns the buckets to.
    return bytes_downloaded(now - dt.timedelta(days=1), now, 3600)


def trip(reason: str) -> dict:
    cfg = cf.get_distribution_config(Id=DIST)
    dc, etag = cfg["DistributionConfig"], cfg["ETag"]
    if not dc["Enabled"]:
        # Already tripped (or disabled by hand). The hourly tick re-checks both thresholds, so
        # mailing here would repeat "already disabled" every hour until the sums drop.
        print(f"circuit breaker: {DIST} already disabled ({reason})")
        return {"tripped": False, "already_disabled": True, "reason": reason}
    dc["Enabled"] = False
    try:
        cf.update_distribution(Id=DIST, IfMatch=etag, DistributionConfig=dc)
    except Exception as e:  # noqa: BLE001 — whatever it is, the owner must hear about it
        sns.publish(TopicArn=TOPIC, Subject="[Soundfont Explorer] circuit breaker FAILED to disable CloudFront",
                    Message=(f"Soundfont Explorer circuit breaker tripped ({reason}) but UpdateDistribution on {DIST} "
                             f"failed:\n\n{e!r}\n\nThe distribution is still ENABLED. Disable it by hand "
                             f"(docs/DEPLOY.md, \"If the circuit breaker trips\") and check the Lambda's logs."))
        raise
    msg = (f"Soundfont Explorer circuit breaker tripped: {reason}\n\n"
           f"Distribution {DIST} is DISABLED. To re-enable after investigating, see docs/DEPLOY.md "
           f"(\"If the circuit breaker trips\"). Note: the hourly check trips it again while the trailing "
           f"24 h or month-to-date egress is still over its threshold.")
    sns.publish(TopicArn=TOPIC, Subject="[Soundfont Explorer] CloudFront disabled by circuit breaker", Message=msg)
    return {"tripped": True, "already_disabled": False, "reason": reason}


def handler(event, _ctx):
    now = dt.datetime.now(dt.timezone.utc)
    # (a) alarm via SNS (the breaker's own mails come through the same topic and are ignored)
    for rec in event.get("Records", []):
        if rec.get("EventSource") == "aws:sns":
            try:
                body = json.loads(rec["Sns"]["Message"])
            except (ValueError, KeyError):
                body = {}
            if not isinstance(body, dict):
                body = {}
            if body.get("NewStateValue") == "ALARM":
                return trip(f"daily egress alarm: {body.get('AlarmName')} — {body.get('NewStateReason')}")
            return {"ignored": body.get("NewStateValue")}
    # (b) scheduled check of both thresholds — the daily one too, so a distribution re-enabled
    # while the trailing 24 h are still over DAILY_GB is disabled again within the hour.
    day = trailing_day_bytes(now)
    if day > DAILY_GB * GB:
        return trip(f"trailing 24 h egress {day / GB:.1f} GB > {DAILY_GB:.0f} GB")
    mtd = month_to_date_bytes(now)
    if mtd > MONTHLY_GB * GB:
        return trip(f"month-to-date egress {mtd / GB:.1f} GB > {MONTHLY_GB:.0f} GB")
    return {"tripped": False, "trailing_day_gb": round(day / GB, 2), "month_to_date_gb": round(mtd / GB, 2)}
