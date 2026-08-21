"""Per-file provenance: which collection each SoundFont in soundfonts/ came from.

    python3 -m catalog.provenance --zip soundfonts/500_Soundfonts_Full_GM_Sets.zip \
        --id archive.org/500-soundfonts-full-gm-sets --title "500 Soundfonts Full GM Sets (Internet Archive)" \
        --url https://archive.org/details/500-soundfonts-full-gm-sets \
        --torrent https://archive.org/download/500-soundfonts-full-gm-sets/500-soundfonts-full-gm-sets_archive.torrent

Matches the on-disk files against the zip's central directory (name, size and CRC-32 — no
extraction) and writes catalog/collections.json: {collections: {id: {...}}, files: {sha256: id}}.
Run it once per source collection; entries accumulate. Fonts without an entry are attributed to
nobody (the UI shows no "from" row) — never guessed.
"""
from __future__ import annotations

import argparse
import json
import os
import zipfile
import zlib
from concurrent.futures import ThreadPoolExecutor

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "collections.json")


def crc32_of(path: str) -> int:
    c = 0
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            c = zlib.crc32(chunk, c)
    return c & 0xFFFFFFFF


def load() -> dict:
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as fh:
            return json.load(fh)
    return {"schema": 1, "collections": {}, "files": {}}


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--zip", required=True)
    ap.add_argument("--id", required=True)
    ap.add_argument("--title", required=True)
    ap.add_argument("--url", required=True)
    ap.add_argument("--torrent")
    ap.add_argument("--root", default="soundfonts")
    ap.add_argument("--scan", default=os.path.join(HERE, "soundfonts.json"), help="sf2scan output (for sha256)")
    a = ap.parse_args(argv)

    with open(a.scan, encoding="utf-8") as fh:
        scan = {f["file"]: f for f in json.load(fh)["fonts"]}
    z = zipfile.ZipFile(a.zip)
    members = {os.path.basename(i.filename): i for i in z.infolist() if i.filename.lower().endswith(".sf2")}
    doc = load()
    doc["collections"][a.id] = {"id": a.id, "title": a.title, "url": a.url, "torrent": a.torrent, "zip": os.path.basename(a.zip)}

    candidates = [f for f, rec in scan.items() if f in members and members[f].file_size == rec["bytes"]]
    with ThreadPoolExecutor(8) as ex:
        crcs = dict(zip(candidates, ex.map(lambda f: crc32_of(os.path.join(a.root, f)), candidates)))
    matched = 0
    for f in candidates:
        if crcs[f] == members[f].CRC:
            doc["files"][scan[f]["sha256"]] = a.id
            matched += 1
    unmatched = sorted(set(scan) - {f for f in candidates if crcs[f] == members[f].CRC})
    with open(OUT, "w", encoding="utf-8") as fh:
        json.dump(doc, fh, indent=1, sort_keys=True)
        fh.write("\n")
    print(f"{a.id}: {matched} of {len(scan)} fonts matched by name+size+crc32; "
          f"{len(unmatched)} not from this collection" + (f": {unmatched[:8]}" if unmatched else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
