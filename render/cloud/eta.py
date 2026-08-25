"""Honest ETAs for a fleet run (#25).

The failure this exists to prevent is reporting a *phase* as the run: "shard 4 finishes in 3
minutes" while four shards have not started, every one of them will take ~25 minutes, and the
instances only scale in some minutes after the last child exits. On 2026-08-25 the visible
progress counter was healthy the whole time and the fleet-at-zero moment was still 40-50 minutes
out.

So there are four different answers, and they must be printed as four different things:

    render      the child currently rendering stops rendering
    child       that child exits (render + its publish tail)
    waves       the shards that have not started yet, at `concurrent` at a time
    fleet zero  the last child exits, the CE is disabled and EC2 scales in

Everything is derived from durations this run has actually observed; before the first child
finishes there is nothing to extrapolate from and every estimate is None rather than a guess.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

# Batch scales in after the last job leaves; measured on this fleet at ~2-4 minutes.
SCALE_IN_LAG_S = 180.0


def _median(xs: list[float]) -> float | None:
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


@dataclass
class RunForecast:
    total_shards: int
    concurrent: int | None
    finished: int
    running: int
    child_durations_s: list[float] = field(default_factory=list)
    publish_tail_s: list[float] = field(default_factory=list)
    running_elapsed_s: list[float] = field(default_factory=list)

    @property
    def pending(self) -> int:
        """Shards that have not started at all."""
        return max(0, self.total_shards - self.finished - self.running)

    @property
    def typical_child_s(self) -> float | None:
        return _median(self.child_durations_s)

    @property
    def typical_tail_s(self) -> float | None:
        return _median(self.publish_tail_s)

    @property
    def waves_remaining(self) -> int | None:
        """Scheduling waves still to come for the shards that have not started."""
        if self.concurrent is None or self.concurrent <= 0:
            return None
        return math.ceil(self.pending / self.concurrent)

    def current_child_s(self) -> float | None:
        """Seconds until the slowest running child exits, from what its peers took."""
        typical = self.typical_child_s
        if typical is None or not self.running_elapsed_s:
            return None
        return max(0.0, typical - min(self.running_elapsed_s))

    def current_render_s(self) -> float | None:
        """Seconds until that child stops RENDERING — earlier than it exits by the publish tail."""
        child = self.current_child_s()
        tail = self.typical_tail_s
        if child is None or tail is None:
            return None
        return max(0.0, child - tail)

    def fleet_zero_s(self) -> float | None:
        """Seconds until nothing is running and the instances are gone.

        The waves that have not started are the part everyone forgets: they cannot overlap the
        running ones, so they add a full child duration each, and only then does scale-in start.
        """
        typical = self.typical_child_s
        child = self.current_child_s()
        waves = self.waves_remaining
        if typical is None or child is None or waves is None:
            return None
        return child + waves * typical + SCALE_IN_LAG_S

    def lines(self, fmt=lambda s: f"{s / 60:.0f}m") -> list[str]:
        """One line per answer; unknown values say so rather than guessing."""
        say = lambda v: fmt(v) if v is not None else "unknown"  # noqa: E731
        out = [
            f"{self.finished}/{self.total_shards} shards done, {self.running} running, {self.pending} not started",
            f"current child: render ~{say(self.current_render_s())}, exits ~{say(self.current_child_s())}",
        ]
        w = self.waves_remaining
        if w:
            out.append(f"{w} scheduling wave(s) still to start ({self.pending} shards at {self.concurrent} at a time)")
        out.append(f"fleet at zero ~{say(self.fleet_zero_s())} (includes ~{SCALE_IN_LAG_S / 60:.0f}m scale-in)")
        return out
