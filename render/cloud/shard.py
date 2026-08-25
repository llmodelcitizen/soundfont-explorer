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


def log(*a):
    print(f"[shard {INDEX}]", *a, flush=True)


def sh(argv, **kw):
    return subprocess.run(argv, check=True, **kw)


def stage_inputs(needed: list[str]) -> None:
    """Pull songs, catalog and the SF2s this shard renders. s5cmd because it saturates the NIC
    where a serial GET loop does not; the full font set is 46 GiB."""
    t = time.monotonic()
    for d, pre in ((SONGS, "songs"), (CATALOG, "catalog")):
        d.mkdir(parents=True, exist_ok=True)
        sh(["s5cmd", "sync", f"s3://{FONTS_BUCKET}/{pre}/*", f"{d}/"])
    FONTS.mkdir(parents=True, exist_ok=True)
    if needed:   # only the fonts this shard needs
        spec = "\n".join(f"cp s3://{FONTS_BUCKET}/soundfonts/{n} {FONTS}/{n}" for n in needed)
        sh(["s5cmd", "run"], input=spec.encode())
    else:        # whole matrix: every variant, so every font
        sh(["s5cmd", "sync", f"s3://{FONTS_BUCKET}/soundfonts/*", f"{FONTS}/"])
    # case-insensitive: five fonts in the corpus are named .SF2, and a `*.sf2` glob silently
    # undercounts them — the same mistake that kept them out of S3 in the first place
    sf2 = [p for p in FONTS.iterdir() if p.suffix.lower() == ".sf2"]
    n = len(sf2)
    b = sum(p.stat().st_size for p in sf2)
    log(f"staged {n} fonts ({b / 2**30:.1f} GiB), songs and catalog in {time.monotonic() - t:.0f}s")


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


def publish_song(song: str) -> bool:
    """Manifest the song, then upload only if it really produced variants.

    The guard is the point: a manifest run against a partially rendered song writes a
    plausible-looking set document with a subset of the variants, and publishing that would
    quietly degrade the song on the live site."""
    t = time.monotonic()
    r = subprocess.run([sys.executable, "-m", "sfr", "--fonts", str(FONTS), "--songs", str(SONGS),
                        "--catalog", str(CATALOG), "--work", str(WORK), "--out", str(OUT),
                        "manifest", "--song", song, "--thorough"],
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
    # objects under a/ c/ s/ are immutable and content-addressed, so they go straight to the
    # site bucket; songs.json is written once at the end by submit.py, not per shard.
    for pre in ("a", "c", "s"):
        d = OUT / "public" / pre
        if d.exists():
            sh(["s5cmd", "sync", "--size-only", f"{d}/", f"s3://{SITE_BUCKET}/{pre}/"])
    log(f"published {song}: {got} variants in {time.monotonic() - t:.0f}s")
    return True


def publisher(songs: list[str], sel: list[str], done: threading.Event, state: dict) -> None:
    """Poll for songs whose jobs have all landed and publish them while rendering continues.

    `state` is shared with main(): "left" = songs not yet attempted, "failed" = songs that did
    not publish. Every failure has to land in "failed": a publish that raised (s5cmd, the
    manifest subprocess, a bad plan count) used to kill this daemon thread silently, and
    main() — which looked only at `failed` — exited 0 with the songs simply missing from the
    site (#12)."""
    left, failed = state["left"], state["failed"]

    def attempt(song: str) -> None:
        try:
            ok = publish_song(song)
        except Exception:
            log(f"!! publishing {song} raised:\n{traceback.format_exc()}")
            ok = False
        if not ok:
            failed.append(song)

    try:
        want = {s: expected(s, sel) for s in songs}
        while left:
            for song in list(left):
                n = len(list((WORK / "renders" / song).glob("*/meta.json"))) if (WORK / "renders" / song).exists() else 0
                if n >= want[song]:
                    left.remove(song)
                    attempt(song)
            if left and not done.wait(20):
                continue
            if done.is_set():
                break
        while left:                  # renderer finished; publish whatever remains
            attempt(left.pop(0))
    except Exception:
        log(f"!! publisher died, {len(left)} song(s) will not be published:\n{traceback.format_exc()}")
        while left:
            failed.append(left.pop(0))


def unexpected_failures(songs: list[str]) -> list[tuple]:
    """Failed jobs whose reason is not `silent`.

    `sfr render` exits 1 if any job failed at all, and `silent` failures are normal (a font with
    no sound for this song — 41 of them across these 12 songs). Propagating that would mark every
    Batch shard FAILED and burn the retry budget re-running finished work, so the shard judges its
    own outcome from the metas instead."""
    bad = []
    for song in songs:
        d = WORK / "renders" / song
        for m in sorted(d.glob("*/meta.json")) if d.exists() else []:
            try:
                j = json.loads(m.read_text())
            except (OSError, ValueError):
                continue
            if j.get("status") == "failed" and j.get("reason") != "silent":
                bad.append((song, j.get("variant"), j.get("reason")))
    return bad


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    sh(["s5cmd", "cp", f"s3://{FONTS_BUCKET}/shards.json", "/scratch/shards.json"])
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
    log(f"render finished rc={rc} in {time.monotonic() - t:.0f}s")

    done.set()
    pub.join(timeout=3600)
    failed = list(state["failed"])
    if pub.is_alive():
        # the process is about to exit and take the daemon thread with it mid-upload
        log(f"!! publisher still running after 3600 s; unpublished: {' '.join(state['left'])}")
        failed += [s for s in state["left"] if s not in failed]

    bad = unexpected_failures(songs)
    if bad:
        log(f"!! {len(bad)} unexpected render failures (not `silent`):")
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
    log(f"shard complete (render rc={rc}; only expected `silent` failures)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
