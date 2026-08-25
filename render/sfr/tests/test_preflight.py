"""render/cloud/preflight.py: the capacity arithmetic that decides whether a run can start (#25).

The numbers here are the ones the 2026-08-25 run actually hit: 8 shards x 96 vCPU against a
256-vCPU regional Spot quota, which is why it ran in four waves through two hosts.
"""
import os
import sys
import unittest

CLOUD = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "cloud")
preflight = None   # bound by setUpModule


def setUpModule():
    """preflight.py lives in render/cloud, not render/sfr. Scoped to the module, never at import
    time: a bare insert runs during DISCOVERY of the whole suite and is never undone (#19)."""
    global preflight
    sys.path.insert(0, CLOUD)
    import preflight as _preflight
    preflight = _preflight


def tearDownModule():
    if CLOUD in sys.path:
        sys.path.remove(CLOUD)


def cap(**kw):
    base = dict(shards=8, vcpus_per_shard=96, ce_max_vcpus=2304, quota_vcpus=256, consumed_vcpus=0)
    base.update(kw)
    return preflight.Capacity(**base)


class CapacityTests(unittest.TestCase):
    def test_the_live_run_that_prompted_the_issue(self):
        c = cap()
        self.assertEqual(c.planned_vcpus, 768)
        self.assertEqual(c.headroom_vcpus, 256)
        self.assertEqual(c.concurrent_shards, 2)      # 256 // 96
        self.assertEqual(c.waves, 4)                  # ceil(8 / 2)
        self.assertTrue(c.ok)
        self.assertTrue(c.degraded)
        self.assertIn("only 2 of 8 shards can run at once", " ".join(c.report()))

    def test_a_raised_quota_starts_every_shard_at_once(self):
        c = cap(quota_vcpus=768)
        self.assertEqual(c.concurrent_shards, 8)
        self.assertEqual(c.waves, 1)
        self.assertFalse(c.degraded)
        self.assertIn("all 8 shards can start together", " ".join(c.report()))

    def test_other_spot_instances_eat_the_headroom(self):
        c = cap(quota_vcpus=768, consumed_vcpus=600)
        self.assertEqual(c.headroom_vcpus, 168)
        self.assertEqual(c.concurrent_shards, 1)
        self.assertEqual(c.waves, 8)

    def test_no_room_at_all_is_a_refusal_not_a_warning(self):
        c = cap(quota_vcpus=64)
        self.assertEqual(c.concurrent_shards, 0)
        self.assertEqual(c.waves, 0)
        self.assertFalse(c.ok)
        self.assertIn("NO shard can start", " ".join(c.report()))

    def test_the_batch_ceiling_can_bind_before_the_quota(self):
        c = cap(quota_vcpus=4096, ce_max_vcpus=288)
        self.assertEqual(c.concurrent_shards, 3)      # 288 // 96, not the quota
        self.assertEqual(c.waves, 3)

    def test_unknown_numbers_never_block_a_run(self):
        c = cap(quota_vcpus=None, ce_max_vcpus=None)
        self.assertIsNone(c.headroom_vcpus)
        self.assertIsNone(c.concurrent_shards)
        self.assertIsNone(c.waves)
        self.assertTrue(c.ok)          # unreadable preflight is not a reason to refuse
        self.assertFalse(c.degraded)
        self.assertIn("submitting blind", " ".join(c.report()))

    def test_consumed_counts_spot_only_and_only_live_instances(self):
        vcpus = {"c7a.24xlarge": 96, "m5.large": 2}
        reservations = [{"Instances": [
            {"InstanceLifecycle": "spot", "InstanceType": "c7a.24xlarge", "State": {"Name": "running"}},
            {"InstanceLifecycle": "spot", "InstanceType": "c7a.24xlarge", "State": {"Name": "pending"}},
            {"InstanceLifecycle": "spot", "InstanceType": "c7a.24xlarge", "State": {"Name": "terminated"}},
            {"InstanceType": "m5.large", "State": {"Name": "running"}},          # on-demand: not counted
            {"InstanceLifecycle": "spot", "InstanceType": "unknown.type", "State": {"Name": "running"}},
        ]}]
        self.assertEqual(preflight.consumed_spot_vcpus(reservations, vcpus), 192)

    def test_vcpus_comes_off_the_job_definition(self):
        jd = {"containerProperties": {"resourceRequirements": [
            {"type": "MEMORY", "value": "180000"}, {"type": "VCPU", "value": "96"}]}}
        self.assertEqual(preflight.vcpus_of(jd), 96)
        self.assertEqual(preflight.vcpus_of({"containerProperties": {"vcpus": 16}}), 16)
        self.assertEqual(preflight.vcpus_of({}), 0)


class PoolTests(unittest.TestCase):
    def test_names_the_type_az_pairs_that_can_never_launch(self):
        offerings = [
            {"InstanceType": "c7a.24xlarge", "Location": "us-east-1a"},
            {"InstanceType": "c7a.24xlarge", "Location": "us-east-1c"},
            {"InstanceType": "c7a.48xlarge", "Location": "us-east-1a"},
            {"InstanceType": "c7a.48xlarge", "Location": "us-east-1b"},
            {"InstanceType": "c7a.48xlarge", "Location": "us-east-1c"},
        ]
        subnets = [{"AvailabilityZone": az} for az in ("us-east-1a", "us-east-1b", "us-east-1c")]
        bad = preflight.unusable_pools(offerings, ["c7a.24xlarge", "c7a.48xlarge"], subnets)
        self.assertEqual(bad, ["c7a.24xlarge is not offered in us-east-1b"])

    def test_nothing_to_say_when_every_pool_is_valid(self):
        offerings = [{"InstanceType": "c7a.48xlarge", "Location": "us-east-1a"}]
        subnets = [{"AvailabilityZone": "us-east-1a"}]
        self.assertEqual(preflight.unusable_pools(offerings, ["c7a.48xlarge"], subnets), [])


class GatherTests(unittest.TestCase):
    """gather() must degrade to None rather than raise: a broken preflight cannot block a run."""

    def fake_aws(self, **overrides):
        def aws(*args):
            key = " ".join(args[:2])
            if key in overrides:
                v = overrides[key]
                if isinstance(v, Exception):
                    raise v
                return v
            return {}
        return aws

    def test_reads_all_four_numbers(self):
        aws = self.fake_aws(**{
            "batch describe-job-definitions": {"jobDefinitions": [
                {"containerProperties": {"resourceRequirements": [{"type": "VCPU", "value": "96"}]}}]},
            "batch describe-compute-environments": {"computeEnvironments": [
                {"computeResources": {"maxvCpus": 2304}}]},
            "service-quotas get-service-quota": {"Quota": {"Value": 256.0}},
            "ec2 describe-instance-types": {"InstanceTypes": [
                {"InstanceType": "c7a.24xlarge", "VCpuInfo": {"DefaultVCpus": 96}}]},
            "ec2 describe-instances": {"Reservations": [{"Instances": [
                {"InstanceLifecycle": "spot", "InstanceType": "c7a.24xlarge", "State": {"Name": "running"}}]}]},
        })
        c = preflight.gather("ce", "jd", 8, aws=aws)
        self.assertEqual((c.vcpus_per_shard, c.ce_max_vcpus, c.quota_vcpus, c.consumed_vcpus), (96, 2304, 256, 96))
        self.assertEqual(c.concurrent_shards, 1)      # (256 - 96) // 96

    def test_an_unreadable_quota_is_not_fatal(self):
        aws = self.fake_aws(**{
            "batch describe-job-definitions": {"jobDefinitions": [
                {"containerProperties": {"resourceRequirements": [{"type": "VCPU", "value": "96"}]}}]},
            "service-quotas get-service-quota": PermissionError("no service-quotas:GetServiceQuota"),
        })
        c = preflight.gather("ce", "jd", 8, aws=aws)
        self.assertIsNone(c.quota_vcpus)
        self.assertTrue(c.ok)

    def test_unusable_pools_for_reads_the_live_compute_environment(self):
        aws = self.fake_aws(**{
            "batch describe-compute-environments": {"computeEnvironments": [{"computeResources": {
                "instanceTypes": ["c7a.24xlarge", "c7a.48xlarge"], "subnets": ["subnet-a", "subnet-b"]}}]},
            "ec2 describe-subnets": {"Subnets": [{"AvailabilityZone": "us-east-1a"},
                                                 {"AvailabilityZone": "us-east-1b"}]},
            "ec2 describe-instance-type-offerings": {"InstanceTypeOfferings": [
                {"InstanceType": "c7a.24xlarge", "Location": "us-east-1a"},
                {"InstanceType": "c7a.48xlarge", "Location": "us-east-1a"},
                {"InstanceType": "c7a.48xlarge", "Location": "us-east-1b"}]},
        })
        self.assertEqual(preflight.unusable_pools_for({"compute_environment": "ce"}, aws=aws),
                         ["c7a.24xlarge is not offered in us-east-1b"])

    def test_unusable_pools_for_is_silent_when_it_cannot_look(self):
        self.assertEqual(preflight.unusable_pools_for({"compute_environment": "ce"},
                                                      aws=self.fake_aws()), [])

    def test_scaling_failures_are_best_effort(self):
        self.assertEqual(preflight.recent_scaling_failures("ce", aws=self.fake_aws()), [])
        aws = self.fake_aws(**{
            "autoscaling describe-auto-scaling-groups": {"AutoScalingGroups": [
                {"AutoScalingGroupName": "AwsBatch-soundfont-explorer-render-abc"}]},
            "autoscaling describe-scaling-activities": {"Activities": [
                {"StatusCode": "Failed", "StatusMessage": "MaxSpotInstanceCountExceeded"},
                {"StatusCode": "Successful", "StatusMessage": "fine"},
                {"StatusCode": "Failed", "StatusMessage": "MaxSpotInstanceCountExceeded"},
            ]},
        })
        self.assertEqual(preflight.recent_scaling_failures("soundfont-explorer-render", aws=aws),
                         ["MaxSpotInstanceCountExceeded"])   # deduped


if __name__ == "__main__":
    unittest.main()
