"""One Batch array child: render a shard of songs, publish each as it completes, exit.

Shape (docs/RENDER.md "Cloud runs"):
  - the shard's songs render as ONE queue, not song by song. order_jobs() is already
    font-major/biggest-first, so a single call keeps every SF2 hot across the shard's songs
    and pays the drain-to-zero tail once instead of once per song.
  - a publisher thread packs and uploads each song the moment its last variant lands, so the
    CPU keeps rendering through what is really I/O. This is the render/publish split.
  - the process exits as soon as the queue drains and uploads flush; Batch scales the
    instance in, and spot bills by the second.
"""
import json, os, pathlib, subprocess, sys, threading, time, traceback
from concurrent.futures import ThreadPoolExecutor

# the image puts render/sfr and render/cloud side by side under /opt and sets PYTHONPATH=/opt, so
# the shard reads the publish header table instead of carrying a copy that could drift (#14)
from sfr.config import RenderSettings
from sfr.publish import IMMUTABLE, OBJECT_KINDS

TP_CEILING_DBTP = RenderSettings().tp_ceiling_dbtp

FONTS_BUCKET = os.environ["SFR_FONTS_BUCKET"]
SITE_BUCKET = os.environ["SFR_SITE_BUCKET"]
INDEX = int(os.environ.get("AWS_BATCH_JOB_ARRAY_INDEX", "0"))
# NB: deliberately not read from SFR_WORK/SFR_FONTS/..., which the image sets to the local
# bind-mount paths (/work, /fonts, /songs). A Batch container has no bind mounts, and inheriting
# those defaults silently put every path on the container overlay instead of /scratch.
SCRATCH = pathlib.Path(os.environ.get("SFR_SCRATCH", "/scratch"))
WORK = SCRATCH / "work"
FONTS = SCRATCH / "fonts"
OUT = SCRATCH / "out"
# songs/ and catalog/ are bind mounts for a local run; in Batch there is nothing to bind, so they
# are staged from the same bucket as the fonts. Keeping them out of the image means the image does
# not have to be rebuilt to add a song, and owner-supplied songs/private/ never enters a registry.
SONGS = SCRATCH / "songs"
CATALOG = SCRATCH / "catalog"
def _allocated_cpus() -> int:
    """os.cpu_count() reports the HOST's CPUs inside a container, not this task's share. Batch
    pins the container to its requested vCPUs via the cgroup, so read the quota and only fall
    back to the host count."""
    for quota, period in (("/sys/fs/cgroup/cpu.max", None),
                          ("/sys/fs/cgroup/cpu/cpu.cfs_quota_us", "/sys/fs/cgroup/cpu/cpu.cfs_period_us")):
        try:
            txt = pathlib.Path(quota).read_text().split()
            q = txt[0]
            if q in ("max", "-1"):
                continue
            per = float(txt[1]) if period is None else float(pathlib.Path(period).read_text())
            n = int(float(q) / per)
            if n > 0:
                return n
        except (OSError, ValueError, IndexError):
            continue
    return os.cpu_count() or 8


WORKERS = int(os.environ.get("SFR_WORKERS", "0")) or _allocated_cpus()
def _allocated_memory() -> int:
    """Bytes this task may use. Like the CPU count, SC_PHYS_PAGES reports the HOST's memory, not
    the cgroup ceiling Batch imposes — reading it granted 327 GiB of admission inside a 342 GiB
    container, leaving 78 MiB per worker for process overhead, and the kernel OOM-killed engines
    (48 'exit' failures on the first real run)."""
    host = os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    for f in ("/sys/fs/cgroup/memory.max", "/sys/fs/cgroup/memory/memory.limit_in_bytes"):
        try:
            v = pathlib.Path(f).read_text().strip()
            if v not in ("max", ""):
                n = int(v)
                if 0 < n < host:
                    return n
        except (OSError, ValueError):
            continue
    return host


# 256 MB admission units. Admission covers the SF2 bytes a job loads; every worker also needs
# room for the engine, ffmpeg and ~150 opusenc processes on top, so reserve a unit per worker
# before dividing rather than taking a flat fraction.
def _mem_units(workers: int) -> int:
    usable = _allocated_memory() - max(8 << 30, workers * (256 << 20))
    return max(64, int(usable / (256 << 20)))


MEM_UNITS = int(os.environ.get("SFR_MEM_UNITS", "0")) or _mem_units(WORKERS)

# --- publish concurrency (#25). The tail used to be one song at a time, one s5cmd process using
# about one core, on a 96-core host: 12-15 minutes of near-idle per shard, paid again per
# scheduling wave. All three knobs scale with the allocation and can be overridden per run.
PUBLISH_POOL = int(os.environ.get("SFR_PUBLISH_POOL", "0")) or max(2, min(8, WORKERS // 8))
MANIFEST_WORKERS = int(os.environ.get("SFR_MANIFEST_WORKERS", "0")) or max(1, WORKERS // PUBLISH_POOL)
UPLOAD_WORKERS = int(os.environ.get("SFR_UPLOAD_WORKERS", "0")) or 32


class Phases:
    """Per-phase wall time and throughput for this child, printed as one JSON line at exit.

    The issue's acceptance criteria are numeric ("no low-utilisation interval longer than 30 s",
    "tail under 10% of shard wall time"), and none of them can be argued about without this."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.songs: dict[str, dict] = {}
        self.stage_s = 0.0
        self.render_s = 0.0
        self.started = time.monotonic()

    def record(self, song: str, **kw) -> None:
        with self.lock:
            self.songs.setdefault(song, {}).update(kw)

    def summary(self, published: int, failed: int) -> dict:
        with self.lock:
            manifest = [v.get("manifest_s", 0.0) for v in self.songs.values()]
            upload = [v.get("upload_s", 0.0) for v in self.songs.values()]
            wall = time.monotonic() - self.started
            # the tail is what happens after rendering stops: that is the number to drive down
            tail = max(0.0, wall - self.stage_s - self.render_s)
            return {
                "shard": INDEX, "wall_s": round(wall, 1),
                "stage_s": round(self.stage_s, 1), "render_s": round(self.render_s, 1),
                "post_render_tail_s": round(tail, 1),
                "tail_fraction": round(tail / wall, 3) if wall else None,
                "manifest_s_total": round(sum(manifest), 1),
                "upload_s_total": round(sum(upload), 1),
                "songs_published": published, "songs_failed": failed,
                "publish_pool": PUBLISH_POOL, "manifest_workers": MANIFEST_WORKERS,
                "upload_workers": UPLOAD_WORKERS, "render_workers": WORKERS,
            }


PHASES = Phases()


def log(*a):
    print(f"[shard {INDEX}]", *a, flush=True)


# s5cmd logs one line per object at its default `info` level. Staging a shard is ~3,240 of
# them (the songs tree plus this shard's fonts) against four lines of the shard's own output:
# 96.6% of the CloudWatch stream was per-object receipts on the 2026-08-25 run, and the Logs
# view had to walk all of it on every 5 s poll. `error` keeps the failures — the only s5cmd
# lines anyone reads — and drops the receipts. Every invocation goes through this list so a
# new call site cannot quietly reintroduce the flood.
S5CMD = ["s5cmd", "--log", "error"]


def sh(argv, **kw):
    return subprocess.run(argv, check=True, **kw)


def stage_inputs(needed: list[str]) -> None:
    """Pull songs, catalog and the SF2s this shard renders. s5cmd because it saturates the NIC
    where a serial GET loop does not; the full font set is 46 GiB."""
    t = time.monotonic()
    for d, pre in ((SONGS, "songs"), (CATALOG, "catalog")):
        d.mkdir(parents=True, exist_ok=True)
        sh([*S5CMD, "sync", f"s3://{FONTS_BUCKET}/{pre}/*", f"{d}/"])
    FONTS.mkdir(parents=True, exist_ok=True)
    if needed:   # only the fonts this shard needs
        spec = "\n".join(f"cp s3://{FONTS_BUCKET}/soundfonts/{n} {FONTS}/{n}" for n in needed)
        sh([*S5CMD, "run"], input=spec.encode())
    else:        # whole matrix: every variant, so every font
        sh([*S5CMD, "sync", f"s3://{FONTS_BUCKET}/soundfonts/*", f"{FONTS}/"])
    # case-insensitive: five fonts in the corpus are named .SF2, and a `*.sf2` glob silently
    # undercounts them — the same mistake that kept them out of S3 in the first place
    sf2 = [p for p in FONTS.iterdir() if p.suffix.lower() == ".sf2"]
    n = len(sf2)
    b = sum(p.stat().st_size for p in sf2)
    PHASES.stage_s = time.monotonic() - t
    log(f"staged {n} fonts ({b / 2**30:.1f} GiB), songs and catalog in {PHASES.stage_s:.0f}s")


def sfr(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "sfr", "--fonts", str(FONTS), "--songs", str(SONGS),
                           "--catalog", str(CATALOG), "--work", str(WORK), "--out", str(OUT), *args],
                          check=False)


def expected(song: str, sel: list[str]) -> int:
    """How many jobs this song plans under the same selection the renderer uses, so the publisher
    knows when the song is finished. Must take `sel`: with an engine filter the song's total is
    not its full variant count, and the publisher would otherwise wait for renders never queued."""
    r = subprocess.run([sys.executable, "-m", "sfr", "--fonts", str(FONTS), "--songs", str(SONGS),
                        "--catalog", str(CATALOG), "--work", str(WORK), "plan", "--song", song, *sel],
                       capture_output=True, text=True, check=True)
    n = int(r.stdout.split(" jobs", 1)[0].strip())
    if n <= 0:
        # never let a bad count mean "everything is already done"
        raise RuntimeError(f"{song}: plan reports {n} jobs — refusing to treat that as complete")
    return n


def sync_commands(public: pathlib.Path, bucket: str, song: str | None = None,
                  workers: int = 0) -> list[list[str]]:
    """`s5cmd sync` commands stamping the Content-Type and Cache-Control sfr.publish prescribes.
    A bare sync sends no Cache-Control at all and the type /etc/mime.types guesses
    (application/x-tex-pk for a .pk), so CloudFront held fleet-published audio for a day instead
    of a year (#14). --include also keeps a stray .pk.tmp out of the bucket.

    With `song`, the source is that song's own subtree (a/<song>/, s/<song>/) rather than the
    accumulated a/ and s/ roots. Publishing song N used to re-walk everything songs 1..N-1 had
    already written — quadratic listing work, on the critical path, per song (#25). The shared
    catalog document under c/ is not song-scoped and rides with the first publish.
    """
    cmds = []
    for pre, glob, ctype in OBJECT_KINDS:
        rel = pre if (song is None or pre == "c") else f"{pre}/{song}"
        d = public / rel
        if not d.exists():
            continue
        cmd = list(S5CMD)
        if workers > 0:
            cmd += ["--numworkers", str(workers)]        # transfer concurrency is a knob, not a default
        cmd += ["sync", "--size-only", "--include", glob, "--content-type", ctype,
                "--cache-control", IMMUTABLE, f"{d}/", f"s3://{bucket}/{rel}/"]
        cmds.append(cmd)
    return cmds


def publish_song(song: str, partial_ok: bool = False) -> bool:
    """Manifest the song, then upload only if it really produced variants.

    The guard is the point: a manifest run against a partially rendered song writes a
    plausible-looking set document with a subset of the variants, and publishing that would
    quietly degrade the song on the live site. `sfr manifest` refuses exactly that (exit 3)
    unless partial_ok — a smoke run with an engine filter or --limit renders a deliberate
    subset and is allowed to publish it."""
    t = time.monotonic()
    r = subprocess.run([sys.executable, "-m", "sfr", "--fonts", str(FONTS), "--songs", str(SONGS),
                        "--catalog", str(CATALOG), "--work", str(WORK), "--out", str(OUT),
                        "manifest", "--song", song, "--thorough",
                        # validation is one opusdec per segment and trivially parallel: give it the
                        # cores this container was allocated instead of manifest.py's default (#25)
                        "--workers", str(MANIFEST_WORKERS),
                        *(["--allow-partial"] if partial_ok else [])],
                       capture_output=True, text=True)
    sys.stdout.write(r.stdout[-4000:])
    if r.returncode:
        log(f"!! manifest failed for {song} rc={r.returncode}: {r.stderr[-500:]}")
        return False
    try:
        report = json.loads(r.stdout[r.stdout.index("{"):])
        got = int(report["songs"][song]["variants"])
    except (ValueError, KeyError, TypeError) as e:
        log(f"!! could not read the manifest report for {song}: {e}"); return False
    if got <= 0:
        log(f"!! {song}: manifest produced {got} variants — NOT publishing")
        return False
    manifest_s = time.monotonic() - t
    # objects under a/ c/ s/ are immutable and content-addressed, so they go straight to the
    # site bucket; songs.json is written once at the end by submit.py, not per shard.
    t_up = time.monotonic()
    for cmd in sync_commands(OUT / "public", SITE_BUCKET, song=song, workers=UPLOAD_WORKERS):
        sh(cmd)
    upload_s = time.monotonic() - t_up
    PHASES.record(song, manifest_s=manifest_s, upload_s=upload_s, variants=got)
    log(f"published {song}: {got} variants in {manifest_s + upload_s:.0f}s "
        f"(manifest {manifest_s:.0f}s, upload {upload_s:.0f}s)")
    return True


def publisher(songs: list[str], sel: list[str], done: threading.Event, state: dict) -> None:
    """Publish songs as they finish rendering, several at once, while rendering continues.

    `state` is shared with main(): "left" = songs not yet handed to the pool, "failed" = songs
    that did not publish. Every failure has to land in "failed": a publish that raised (s5cmd,
    the manifest subprocess, a bad plan count) used to kill this daemon thread silently, and
    main() — which looked only at `failed` — exited 0 with the songs simply missing from the
    site (#12).

    The pool is what stops the tail being serial (#25). Order still matters for cache locality
    while rendering, but a song that is *ready* need not wait for the previous song's upload:
    manifest is CPU work and s5cmd is network work, so several overlap happily.
    """
    left, failed = state["left"], state["failed"]
    lock = threading.Lock()

    def attempt(song: str) -> None:
        try:
            ok = publish_song(song, partial_ok=bool(sel))
        except Exception:
            log(f"!! publishing {song} raised:\n{traceback.format_exc()}")
            ok = False
        if not ok:
            with lock:
                failed.append(song)

    pool = ThreadPoolExecutor(max_workers=PUBLISH_POOL, thread_name_prefix="publish")
    futures: list = []
    try:
        want = {s: expected(s, sel) for s in songs}
        while left:
            for song in list(left):
                d = WORK / "renders" / song
                n = len(list(d.glob("*/meta.json"))) if d.exists() else 0
                if n >= want[song]:
                    left.remove(song)
                    futures.append(pool.submit(attempt, song))
            if left and not done.wait(20):
                continue
            if done.is_set():
                break
        while left:                  # renderer finished; publish whatever remains
            futures.append(pool.submit(attempt, left.pop(0)))
    except Exception:
        log(f"!! publisher died, {len(left)} song(s) will not be published:\n{traceback.format_exc()}")
        with lock:
            while left:
                failed.append(left.pop(0))
    finally:
        # a song handed to the pool is no longer in `left`, so main() cannot see it as unpublished:
        # every future has to be waited on here, inside the thread main() joins.
        pool.shutdown(wait=True)


# A failed job with one of these reasons did not break anything: that VARIANT is excluded and
# the song publishes without it. They are outcomes of the pipeline working, not faults.
#
#   silent       a font with no sound for this song (41 across the 12 songs of the first run)
#   peak-unsafe  #27's true-peak ceiling refusing a master that would clip. Dropping the variant
#                is the entire point of the gate; publishing it would be the bug.
#
# `sfr render` exits 1 if ANY job failed, so the shard judges its own outcome from the metas
# instead — otherwise every child is FAILED and the retry budget re-runs finished work. When
# peak-unsafe was missing from this list it did exactly that: 5 of the 8 shards on the
# 2026-08-25 run reported FAILED to Batch after publishing every song they were given.
EXCLUSIONS = ("silent", "peak-unsafe")


def failure_report(songs: list[str]) -> tuple[list[tuple], dict[str, list]]:
    """(failures that mean the shard is broken, per-reason lists of excluded variants).

    Excluded variants are returned rather than counted so main() can report HOW FAR over the
    ceiling a peak-unsafe master landed. That number is the difference between "the limiter
    overshot by a hundredth of a dB and the tolerance is too tight" and "this render is 30 dB
    hot", and without it the log says only that some variants did not publish."""
    bad: list[tuple] = []
    excluded: dict[str, list] = {}
    for song in songs:
        d = WORK / "renders" / song
        for m in sorted(d.glob("*/meta.json")) if d.exists() else []:
            try:
                j = json.loads(m.read_text())
            except (OSError, ValueError):
                continue
            if j.get("status") != "failed":
                continue
            reason = j.get("reason")
            if reason in EXCLUSIONS:
                excluded.setdefault(reason, []).append((song, j.get("variant"), j.get("output_tp")))
            else:
                bad.append((song, j.get("variant"), reason))
    return bad, excluded


def excluded_line(excluded: dict[str, list]) -> str:
    """One line naming every exclusion, with the worst peak overshoot spelled out."""
    parts = []
    for reason in sorted(excluded):
        rows = excluded[reason]
        tps = [tp for _, _, tp in rows if isinstance(tp, (int, float))]
        detail = f" (worst {max(tps):.3f} dBTP vs {TP_CEILING_DBTP} ceiling)" if tps else ""
        parts.append(f"{len(rows)} {reason}{detail}")
    return ", ".join(parts)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    sh([*S5CMD, "cp", f"s3://{FONTS_BUCKET}/shards.json", "/scratch/shards.json"])
    shards = json.loads(pathlib.Path("/scratch/shards.json").read_text())
    songs = shards[INDEX]["songs"]
    log(f"{len(songs)} songs: {' '.join(songs)}  workers={WORKERS} mem_units={MEM_UNITS}")
    stage_inputs(shards[INDEX].get("fonts", []))

    # optional narrowing, for smoke runs and partial re-renders
    sel = [a for e in shards[INDEX].get("engines", []) for a in ("--engine", e)]
    if shards[INDEX].get("limit"):
        sel += ["--limit", str(shards[INDEX]["limit"])]

    done = threading.Event()
    state = {"left": list(songs), "failed": []}
    pub = threading.Thread(target=publisher, args=(songs, sel, done, state), daemon=True)
    pub.start()

    t = time.monotonic()
    songsel = [a for s in songs for a in ("--song", s)]
    rc = sfr("render", *songsel, *sel, "--workers", str(WORKERS),
             "--mem-units", str(MEM_UNITS)).returncode
    PHASES.render_s = time.monotonic() - t
    log(f"render finished rc={rc} in {PHASES.render_s:.0f}s")

    done.set()
    pub.join(timeout=3600)
    failed = list(state["failed"])
    if pub.is_alive():
        # the process is about to exit and take the daemon thread with it mid-upload
        log(f"!! publisher still running after 3600 s; unpublished: {' '.join(state['left'])}")
        failed += [s for s in state["left"] if s not in failed]

    published = len(songs) - len(failed)
    # one machine-readable line per child: the acceptance criteria in #25 are numeric and this is
    # what they are measured from (tail fraction, per-phase wall time, concurrency actually used)
    log("phases " + json.dumps(PHASES.summary(published, len(failed))))

    bad, excluded = failure_report(songs)
    if excluded:
        # not a failure: say so plainly, with the numbers, every run
        log(f"excluded {sum(len(v) for v in excluded.values())} variant(s): {excluded_line(excluded)}")
    if bad:
        log(f"!! {len(bad)} unexpected render failures (not {'/'.join(EXCLUSIONS)}):")
        for song, var, reason in bad[:40]:
            log(f"     {song}/{var}: {reason}")
        errs = WORK / "errors.log"
        if errs.exists():
            log("-- tail of work/errors.log --")
            sys.stdout.write("\n".join(errs.read_text(errors="replace").splitlines()[-60:]) + "\n")
    if failed:
        log(f"!! shard failed to publish: {' '.join(failed)}")
        return 1
    if bad:
        return 1
    log(f"shard complete (render rc={rc}; only expected exclusions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
