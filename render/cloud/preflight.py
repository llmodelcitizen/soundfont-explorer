"""Can the fleet actually run the shards we are about to submit?

Batch will happily accept an 8-shard array the account cannot run: the compute environment's
`maxvCpus` is a Batch-side ceiling, but EC2 enforces a *separate* regional Spot vCPU quota, and
Auto Scaling just retries `MaxSpotInstanceCountExceeded` forever while the array sits RUNNABLE.
On 2026-08-25 that turned an 8 x 96 vCPU run (768 vCPU) into four sequential waves through two
hosts, paying every shard's publish tail again per wave (#25).

Nothing here mutates anything: it reads four numbers and does arithmetic on them.

    planned    shards x vcpus_per_shard
    ce_max     the compute environment's maxvCpus
    quota      the regional "All Standard Spot Instance Requests" vCPU quota (L-34B43A08)
    consumed   Spot vCPUs already running/pending in the region, ours or not

The answer that matters is how many shards can run AT ONCE, and therefore how many scheduling
waves the run really takes — which is what an honest ETA has to be built from.
"""
from __future__ import annotations

import json
import math
import subprocess
from dataclasses import dataclass

# "All Standard (A, C, D, H, I, M, R, T, Z) Spot Instance Requests", in vCPUs.
SPOT_VCPU_QUOTA_CODE = "L-34B43A08"


# What the ECS agent and the OS need on top of the container's own request. Only matters when
# deciding whether TWO shards fit on one instance, which is why a rough reserve is enough.
AGENT_MEMORY_MIB = 2048


@dataclass
class Capacity:
    shards: int
    vcpus_per_shard: int
    ce_max_vcpus: int | None
    quota_vcpus: int | None
    consumed_vcpus: int
    # Optional, and worth having: the Spot quota counts the vCPUs of whole INSTANCES, not the
    # vCPUs a container asks for. A 30-vCPU shard on a 96-vCPU instance still spends 96 of the
    # quota (Batch may pack three shards onto it); without the shapes we can only assume one
    # shard's request is one shard's worth of quota, which understates big-instance waste.
    shard_memory_mib: int | None = None
    instance_shapes: tuple[tuple[str, int, int], ...] = ()      # (name, vcpus, memory_mib)

    @property
    def planned_vcpus(self) -> int:
        return self.shards * self.vcpus_per_shard

    @property
    def headroom_vcpus(self) -> int | None:
        """Spot vCPUs this run may still claim, or None when the quota could not be read."""
        if self.quota_vcpus is None:
            return None
        return max(0, self.quota_vcpus - self.consumed_vcpus)

    def shards_per_instance(self, vcpus: int, memory_mib: int) -> int:
        """How many shards Batch can pack onto one instance of this shape."""
        if self.vcpus_per_shard <= 0:
            return 0
        by_cpu = vcpus // self.vcpus_per_shard
        if not self.shard_memory_mib:
            return by_cpu
        by_mem = max(0, memory_mib - AGENT_MEMORY_MIB) // self.shard_memory_mib
        return min(by_cpu, by_mem)

    @property
    def best_shape(self) -> tuple[str, int, int] | None:
        """The configured instance shape that lets the most shards run inside the limits."""
        limits = [x for x in (self.headroom_vcpus, self.ce_max_vcpus) if x is not None]
        if not self.instance_shapes or not limits:
            return None
        budget = min(limits)
        best, best_n = None, -1
        for name, vcpus, mem in self.instance_shapes:
            per = self.shards_per_instance(vcpus, mem)
            if per <= 0 or vcpus <= 0:
                continue
            n = min(self.shards, (budget // vcpus) * per)
            if n > best_n:
                best, best_n = (name, vcpus, mem), n
        return best

    @property
    def concurrent_shards(self) -> int | None:
        """How many shards can be RUNNING at once. None when nothing limits us that we can see."""
        limits = [x for x in (self.headroom_vcpus, self.ce_max_vcpus) if x is not None]
        if not limits or self.vcpus_per_shard <= 0:
            return None
        budget = min(limits)
        shape = self.best_shape
        if shape is None:
            return budget // self.vcpus_per_shard      # no shapes known: request-sized estimate
        _, vcpus, mem = shape
        return min(self.shards, (budget // vcpus) * self.shards_per_instance(vcpus, mem))

    @property
    def waves(self) -> int | None:
        """Scheduling waves the run takes. 1 means every shard starts together."""
        n = self.concurrent_shards
        if n is None:
            return None
        return math.ceil(self.shards / n) if n > 0 else 0

    def report(self) -> list[str]:
        """Operator-facing lines; the caller decides whether they are a warning or a refusal."""
        say = lambda v: "unknown" if v is None else str(v)  # noqa: E731
        out = [
            f"planned {self.planned_vcpus} vCPU ({self.shards} shards x {self.vcpus_per_shard})",
            f"compute environment maxvCpus {say(self.ce_max_vcpus)}",
            f"regional Spot quota {say(self.quota_vcpus)} vCPU, {self.consumed_vcpus} already in use"
            f" -> {say(self.headroom_vcpus)} free",
        ]
        shape = self.best_shape
        if shape is not None:
            name, vcpus, mem = shape
            per = self.shards_per_instance(vcpus, mem)
            out.append(f"best fit {name} ({vcpus} vCPU): {per} shard(s) per instance, "
                       f"so each running shard costs {vcpus // max(1, per)} vCPU of quota")
        n, w = self.concurrent_shards, self.waves
        if n is None:
            out.append("could not determine concurrency — submitting blind")
        elif n <= 0:
            out.append("NO shard can start: not one shard's worth of Spot vCPUs is available")
        elif n >= self.shards:
            out.append(f"all {self.shards} shards can start together")
        else:
            out.append(f"only {n} of {self.shards} shards can run at once -> {w} scheduling waves,"
                       f" and every wave pays its own publish tail")
        return out

    @property
    def ok(self) -> bool:
        """True when the run can start at least one shard. Fewer than requested is a warning."""
        n = self.concurrent_shards
        return n is None or n > 0

    @property
    def degraded(self) -> bool:
        n = self.concurrent_shards
        return n is not None and 0 < n < self.shards


def vcpus_of(job_definition: dict) -> int:
    """The vCPUs one shard asks for, from a Batch job definition document."""
    props = job_definition.get("containerProperties") or {}
    for r in props.get("resourceRequirements") or []:
        if r.get("type") == "VCPU":
            return int(float(r["value"]))
    return int(props.get("vcpus") or 0)


def memory_of(job_definition: dict) -> int:
    """The memory one shard asks for, in MiB, from a Batch job definition document."""
    props = job_definition.get("containerProperties") or {}
    for r in props.get("resourceRequirements") or []:
        if r.get("type") == "MEMORY":
            return int(float(r["value"]))
    return int(props.get("memory") or 0)


def consumed_spot_vcpus(reservations: list[dict], vcpu_by_type: dict[str, int]) -> int:
    """Spot vCPUs running or pending in the region — including instances that are not ours.

    The quota counts every Spot request in the account/region, so a neighbouring project's
    fleet reduces what this run can start just as much as our own leftovers do.
    """
    total = 0
    for res in reservations:
        for inst in res.get("Instances", []):
            if inst.get("InstanceLifecycle") != "spot":
                continue
            if (inst.get("State") or {}).get("Name") not in ("pending", "running"):
                continue
            total += vcpu_by_type.get(inst.get("InstanceType", ""), 0)
    return total


def unusable_pools(offerings: list[dict], instance_types: list[str], subnets: list[dict]) -> list[str]:
    """(type, az) pairs the compute environment asks for that the region does not offer.

    `c7a.24xlarge is not supported in us-east-1b` is not a transient capacity message: that AZ
    does not offer the type at all, so every launch into it fails for the life of the fleet.
    """
    offered: dict[str, set[str]] = {}
    for o in offerings:
        offered.setdefault(o["InstanceType"], set()).add(o["Location"])
    azs = sorted({s["AvailabilityZone"] for s in subnets})
    bad = []
    for t in instance_types:
        have = offered.get(t, set())
        for az in azs:
            if az not in have:
                bad.append(f"{t} is not offered in {az}")
    return bad


# ---------------------------------------------------------------- AWS reads (thin, injectable)

def _aws(*args: str) -> dict:
    r = subprocess.run(["aws", *args, "--output", "json"], capture_output=True, text=True, check=True)
    return json.loads(r.stdout) if r.stdout.strip() else {}


def gather(compute_environment: str, job_definition: str, shards: int, *, aws=_aws,
           shard_vcpus: int | None = None, shard_memory_mib: int | None = None,
           instance_types: list[str] | None = None) -> Capacity:
    """Read what we can; anything unreadable becomes None rather than an exception — a preflight
    that cannot run must not be the reason a legitimate run is refused.

    The overrides answer the question the operator is actually asking: not "what would the
    deployed defaults do" but "what will THESE knobs do", which is the pair of numbers the
    admin UI puts next to the estimate before anything is submitted.
    """
    def maybe(fn, default=None):
        try:
            return fn()
        except Exception:
            return default

    # `--job-definitions` wants an ARN or name:revision and returns [] for a bare name, which read
    # as "0 vCPU per shard" and disabled the whole preflight. submit.py submits by NAME, so Batch
    # uses the highest ACTIVE revision — inspect exactly that one.
    jd = maybe(lambda: max(aws("batch", "describe-job-definitions", "--job-definition-name",
                               job_definition, "--status", "ACTIVE")["jobDefinitions"],
                           key=lambda d: d.get("revision", 0)), {})
    ce = maybe(lambda: aws("batch", "describe-compute-environments",
                           "--compute-environments", compute_environment)["computeEnvironments"][0], {})
    quota = maybe(lambda: int(float(aws("service-quotas", "get-service-quota", "--service-code", "ec2",
                                        "--quota-code", SPOT_VCPU_QUOTA_CODE)["Quota"]["Value"])))
    reservations = maybe(lambda: aws("ec2", "describe-instances", "--filters",
                                     "Name=instance-state-name,Values=pending,running")["Reservations"], [])
    # the shapes worth describing: what this run would launch, plus whatever is already running
    # (needed to price the quota it is consuming). Describing every type in the region was a
    # multi-megabyte response for two numbers.
    want = list(instance_types or ((ce or {}).get("computeResources") or {}).get("instanceTypes") or [])
    running = {i.get("InstanceType") for r in (reservations or []) for i in r.get("Instances", [])
               if i.get("InstanceLifecycle") == "spot"}
    names = sorted({n for n in [*want, *running] if n and "." in n})
    types = maybe(lambda: aws("ec2", "describe-instance-types",
                              "--instance-types", *names)["InstanceTypes"], []) if names else []
    vcpu_by_type = {t["InstanceType"]: int(t["VCpuInfo"]["DefaultVCpus"]) for t in (types or [])}
    shapes = tuple((t["InstanceType"], int(t["VCpuInfo"]["DefaultVCpus"]),
                    int(t["MemoryInfo"]["SizeInMiB"]))
                   for t in (types or []) if t["InstanceType"] in set(want))
    return Capacity(
        shards=shards,
        vcpus_per_shard=int(shard_vcpus or vcpus_of(jd or {})),
        ce_max_vcpus=((ce or {}).get("computeResources") or {}).get("maxvCpus"),
        quota_vcpus=quota,
        consumed_vcpus=consumed_spot_vcpus(reservations or [], vcpu_by_type),
        shard_memory_mib=int(shard_memory_mib or memory_of(jd or {}) or 0) or None,
        instance_shapes=shapes,
    )


def unusable_pools_for(render_fleet: dict, *, aws=_aws) -> list[str]:
    """The (type, AZ) pairs this compute environment is configured for but the region never
    offers. Reads the CE's own instance types and subnets, so it stays true after a retune."""
    try:
        ce = aws("batch", "describe-compute-environments",
                 "--compute-environments", render_fleet["compute_environment"])["computeEnvironments"][0]
        cr = ce.get("computeResources") or {}
        types = [t for t in (cr.get("instanceTypes") or []) if "." in t]   # skip families like "optimal"
        subnet_ids = cr.get("subnets") or []
        if not types or not subnet_ids:
            return []
        subnets = aws("ec2", "describe-subnets", "--subnet-ids", *subnet_ids)["Subnets"]
        offerings = aws("ec2", "describe-instance-type-offerings", "--location-type", "availability-zone",
                        "--filters", f"Name=instance-type,Values={','.join(types)}")["InstanceTypeOfferings"]
    except Exception:
        return []
    return unusable_pools(offerings, types, subnets)


def recent_scaling_failures(compute_environment: str, *, aws=_aws, limit: int = 5) -> list[str]:
    """Auto Scaling activities that failed, newest first — why the array is still RUNNABLE.

    Batch hides its ASG, so this walks CE -> ecsClusterArn -> the ASG whose name carries the CE
    name. Best-effort: an empty list means "nothing to show", never "nothing is wrong".
    """
    try:
        groups = aws("autoscaling", "describe-auto-scaling-groups")["AutoScalingGroups"]
        name = next((g["AutoScalingGroupName"] for g in groups
                     if compute_environment.split("/")[-1] in g["AutoScalingGroupName"]), None)
        if not name:
            return []
        acts = aws("autoscaling", "describe-scaling-activities",
                   "--auto-scaling-group-name", name, "--max-items", str(limit * 4))["Activities"]
    except Exception:
        return []
    out = []
    for a in acts:
        if a.get("StatusCode") in ("Failed", "Cancelled"):
            msg = (a.get("StatusMessage") or a.get("Description") or "").strip()
            if msg and msg not in out:
                out.append(msg)
        if len(out) >= limit:
            break
    return out
