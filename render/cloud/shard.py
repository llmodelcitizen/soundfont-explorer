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
import json, os, pathlib, subprocess, sys, threading, time

FONTS_BUCKET = os.environ["SFR_FONTS_BUCKET"]
SITE_BUCKET = os.environ["SFR_SITE_BUCKET"]
INDEX = int(os.environ.get("AWS_BATCH_JOB_ARRAY_INDEX", "0"))
WORK = pathlib.Path(os.environ.get("SFR_WORK", "/scratch"))
FONTS = pathlib.Path(os.environ.get("SFR_FONTS", "/scratch/fonts"))
OUT = pathlib.Path(os.environ.get("SFR_OUT", "/scratch/out"))
# songs/ and catalog/ are bind mounts for a local run; in Batch there is nothing to bind, so they
# are staged from the same bucket as the fonts. Keeping them out of the image means the image does
# not have to be rebuilt to add a song, and owner-supplied songs/private/ never enters a registry.
SONGS = pathlib.Path(os.environ.get("SFR_SONGS", "/scratch/songs"))
CATALOG = pathlib.Path(os.environ.get("SFR_CATALOG", "/scratch/catalog"))
WORKERS = int(os.environ.get("SFR_WORKERS", str(os.cpu_count() or 8)))
# 256 MB admission units; leave ~12% of RAM for page cache and the encoders
MEM_UNITS = int(os.environ.get("SFR_MEM_UNITS", "0")) or max(
    64, int(os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES") * 0.88 / (256 << 20)))


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
    n = sum(1 for _ in FONTS.glob("*.sf2"))
    b = sum(p.stat().st_size for p in FONTS.glob("*.sf2"))
    log(f"staged {n} fonts ({b / 2**30:.1f} GiB), songs and catalog in {time.monotonic() - t:.0f}s")


def sfr(*args: str) -> subprocess.CompletedProcess:
    return subprocess.run([sys.executable, "-m", "sfr", "--fonts", str(FONTS), "--songs", str(SONGS),
                           "--catalog", str(CATALOG), "--work", str(WORK), "--out", str(OUT), *args],
                          check=False)


def expected(song: str) -> int:
    """How many jobs this song plans, so the publisher knows when the song is finished."""
    r = subprocess.run([sys.executable, "-m", "sfr", "--fonts", str(FONTS), "--songs", str(SONGS),
                        "--catalog", str(CATALOG), "--work", str(WORK), "--song", song, "plan"],
                       capture_output=True, text=True, check=True)
    return int(r.stdout.split(" jobs", 1)[0].strip())


def publish_song(song: str) -> None:
    t = time.monotonic()
    if sfr("manifest", "--song", song, "--thorough").returncode:
        log(f"!! manifest failed for {song}"); return
    # objects under a/ c/ s/ are immutable and content-addressed, so they go straight to the
    # site bucket; songs.json is written once at the end by submit.py, not per shard.
    for pre in ("a", "c", "s"):
        d = OUT / "public" / pre
        if d.exists():
            sh(["s5cmd", "sync", "--size-only", f"{d}/", f"s3://{SITE_BUCKET}/{pre}/"])
    log(f"published {song} in {time.monotonic() - t:.0f}s")


def publisher(songs: list[str], done: threading.Event) -> None:
    """Poll for songs whose jobs have all landed and publish them while rendering continues."""
    want = {s: expected(s) for s in songs}
    left = list(songs)
    while left:
        for song in list(left):
            n = len(list((WORK / "renders" / song).glob("*/meta.json"))) if (WORK / "renders" / song).exists() else 0
            if n >= want[song]:
                left.remove(song)
                publish_song(song)
        if left and not done.wait(20):
            continue
        if done.is_set():
            break
    for song in left:            # renderer finished; publish whatever remains
        publish_song(song)


def main() -> int:
    WORK.mkdir(parents=True, exist_ok=True)
    sh(["s5cmd", "cp", f"s3://{FONTS_BUCKET}/shards.json", "/scratch/shards.json"])
    shards = json.loads(pathlib.Path("/scratch/shards.json").read_text())
    songs = shards[INDEX]["songs"]
    log(f"{len(songs)} songs: {' '.join(songs)}  workers={WORKERS} mem_units={MEM_UNITS}")
    stage_inputs(shards[INDEX].get("fonts", []))

    done = threading.Event()
    pub = threading.Thread(target=publisher, args=(songs, done), daemon=True)
    pub.start()

    t = time.monotonic()
    sel = [a for s in songs for a in ("--song", s)]
    rc = sfr("render", *sel, "--workers", str(WORKERS), "--mem-units", str(MEM_UNITS)).returncode
    log(f"render finished rc={rc} in {time.monotonic() - t:.0f}s")

    done.set()
    pub.join(timeout=3600)
    log("shard complete")
    return rc


if __name__ == "__main__":
    sys.exit(main())
