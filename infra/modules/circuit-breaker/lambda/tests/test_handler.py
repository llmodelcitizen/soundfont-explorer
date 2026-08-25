"""Decision logic of the egress circuit breaker Lambda. Stdlib only: boto3 is not installed
in CI, so a stub module is planted before `handler` is imported and the three clients are
replaced with Mocks per test."""
import datetime as dt
import json
import os
import sys
import types
import unittest
from unittest import mock

os.environ["DISTRIBUTION_ID"] = "EDIST"
os.environ["TOPIC_ARN"] = "arn:aws:sns:us-east-1:1:alerts"
os.environ["DAILY_GB"] = "150"
os.environ["MONTHLY_GB"] = "900"
_boto3 = types.ModuleType("boto3")
_boto3.client = lambda *_a, **_kw: mock.Mock(name="unused-client")
sys.modules["boto3"] = _boto3
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), ".."))
import handler  # noqa: E402

GB = handler.GB
NOW = dt.datetime(2026, 8, 24, 10, 30, tzinfo=dt.timezone.utc)


def sns_event(message) -> dict:
    body = message if isinstance(message, str) else json.dumps(message)
    return {"Records": [{"EventSource": "aws:sns", "Sns": {"Message": body}}]}


class BreakerTestCase(unittest.TestCase):
    def setUp(self):
        self.cf = mock.Mock(name="cloudfront")
        self.cw = mock.Mock(name="cloudwatch")
        self.sns = mock.Mock(name="sns")
        self.cf.get_distribution_config.return_value = {
            "DistributionConfig": {"Enabled": True, "Comment": "site"}, "ETag": "E1"}
        for name, client in (("cf", self.cf), ("cw", self.cw), ("sns", self.sns)):
            p = mock.patch.object(handler, name, client)
            p.start()
            self.addCleanup(p.stop)

    def metrics(self, day_gb: float, month_gb: float) -> None:
        """Pin what the two metric windows report (the windows themselves are tested below)."""
        for name, gb in (("trailing_day_bytes", day_gb), ("month_to_date_bytes", month_gb)):
            p = mock.patch.object(handler, name, return_value=gb * GB)
            p.start()
            self.addCleanup(p.stop)

    def assert_tripped(self, result: dict) -> None:
        self.assertTrue(result["tripped"])
        self.cf.update_distribution.assert_called_once()
        kw = self.cf.update_distribution.call_args.kwargs
        self.assertEqual((kw["Id"], kw["IfMatch"], kw["DistributionConfig"]["Enabled"]), ("EDIST", "E1", False))
        self.sns.publish.assert_called_once()
        self.assertIn("disabled by circuit breaker", self.sns.publish.call_args.kwargs["Subject"])

    def assert_untouched(self) -> None:
        self.cf.update_distribution.assert_not_called()
        self.sns.publish.assert_not_called()


class HourlyPathTests(BreakerTestCase):
    def test_trips_when_trailing_day_over_threshold(self):
        # The daily alarm only fires on OK->ALARM; after a manual re-enable the hourly tick
        # must re-trip while the trailing 24 h are still over egress_daily_gb.
        self.metrics(day_gb=151, month_gb=200)
        r = handler.handler({}, None)
        self.assert_tripped(r)
        self.assertIn("24 h", r["reason"])
        self.assertIn("151.0 GB > 150 GB", r["reason"])

    def test_trips_when_month_to_date_over_threshold(self):
        self.metrics(day_gb=10, month_gb=901)
        r = handler.handler({}, None)
        self.assert_tripped(r)
        self.assertIn("month-to-date", r["reason"])

    def test_under_both_thresholds_does_nothing(self):
        self.metrics(day_gb=149.99, month_gb=899.99)
        r = handler.handler({}, None)
        self.assertEqual(r, {"tripped": False, "trailing_day_gb": 149.99, "month_to_date_gb": 899.99})
        self.assert_untouched()

    def test_already_disabled_is_neither_updated_nor_mailed(self):
        # Every tick re-checks both thresholds, so a tripped distribution must not produce
        # an "already disabled" mail per hour until the sums drop.
        self.cf.get_distribution_config.return_value["DistributionConfig"]["Enabled"] = False
        self.metrics(day_gb=500, month_gb=1000)
        r = handler.handler({}, None)
        self.assertEqual((r["tripped"], r["already_disabled"]), (False, True))
        self.assert_untouched()


class AlarmPathTests(BreakerTestCase):
    def test_alarm_record_trips_without_querying_metrics(self):
        r = handler.handler(sns_event({"NewStateValue": "ALARM", "AlarmName": "daily", "NewStateReason": "x"}), None)
        self.assert_tripped(r)
        self.assertIn("daily", r["reason"])
        self.cw.get_metric_statistics.assert_not_called()

    def test_other_transitions_are_ignored(self):
        for state in ("OK", "INSUFFICIENT_DATA"):
            r = handler.handler(sns_event({"NewStateValue": state}), None)
            self.assertEqual(r, {"ignored": state})
        self.assert_untouched()

    def test_non_alarm_messages_are_ignored(self):
        # The breaker's own mails pass through the same topic, and nothing that is not an
        # alarm notification may crash the invocation (a crash would land on the failure topic).
        for message in ("Soundfont Explorer circuit breaker tripped: plain text", {"requestContext": {}}, [1, 2], "42"):
            r = handler.handler(sns_event(message), None)
            self.assertEqual(r, {"ignored": None})
        self.assert_untouched()


class FailedTripTests(BreakerTestCase):
    def test_failed_disable_is_mailed_and_raised(self):
        # Async invocations are retried and then dropped: a trip that could not disable the
        # distribution has to reach the owner and still fail the invocation.
        self.metrics(day_gb=151, month_gb=0)
        self.cf.update_distribution.side_effect = RuntimeError("PreconditionFailed")
        with self.assertRaises(RuntimeError):
            handler.handler({}, None)
        self.sns.publish.assert_called_once()
        kw = self.sns.publish.call_args.kwargs
        self.assertEqual(kw["TopicArn"], "arn:aws:sns:us-east-1:1:alerts")
        self.assertIn("FAILED", kw["Subject"])
        self.assertIn("PreconditionFailed", kw["Message"])
        self.assertIn("still ENABLED", kw["Message"])


class MetricWindowTests(BreakerTestCase):
    def test_trailing_day_window(self):
        self.cw.get_metric_statistics.return_value = {"Datapoints": [{"Sum": 3.0}, {"Sum": 4.0}]}
        self.assertEqual(handler.trailing_day_bytes(NOW), 7.0)
        kw = self.cw.get_metric_statistics.call_args.kwargs
        self.assertEqual((kw["StartTime"], kw["EndTime"], kw["Period"]), (NOW - dt.timedelta(days=1), NOW, 3600))
        self.assertEqual((kw["Namespace"], kw["MetricName"], kw["Statistics"]), ("AWS/CloudFront", "BytesDownloaded", ["Sum"]))
        self.assertIn({"Name": "DistributionId", "Value": "EDIST"}, kw["Dimensions"])

    def test_month_to_date_window(self):
        self.cw.get_metric_statistics.return_value = {}
        self.assertEqual(handler.month_to_date_bytes(NOW), 0.0)
        kw = self.cw.get_metric_statistics.call_args.kwargs
        self.assertEqual((kw["StartTime"], kw["EndTime"], kw["Period"]),
                         (dt.datetime(2026, 8, 1, tzinfo=dt.timezone.utc), NOW, 86400))


if __name__ == "__main__":
    unittest.main()
