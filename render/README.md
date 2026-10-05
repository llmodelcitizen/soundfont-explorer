# render

Turns every song × variant pair into audio the player can stream. Each render is:

1. played by its synth engine
2. matched in loudness (−16 LUFS, with a safe peak ceiling)
3. cut into short Opus slices in two tiers: small "scrub" slices for fast switching and longer
   "listen" slices for quality
4. packed and described by JSON manifests the website reads

All engines live in one Docker image (`sfr-render`). You don't install any synths on your
machine.

## Layout

| Path | What it is |
|---|---|
| `Dockerfile` | The render image, built from pinned engine versions |
| `engines.json` | Engine versions, flags, timing offsets and the render settings |
| `sfr/` | The pipeline (`sfr render`, `manifest`, `publish`, …). Python standard library only. |
| `cloud/` | Runs renders on the AWS Batch fleet (`submit.py`) |
| `scripts/` | `full-run.sh` (local song-by-song driver) and early experiments (`derisk/`) |
| `edmidi-render/` | Our small front end for the MSX engine |
| `banks/` | Extra FM banks to copy into the image |

## Engines

| Engine | Sounds like | Needs ROMs? |
|---|---|---|
| FluidSynth | Any SF2 SoundFont (most variants) | no |
| libADLMIDI | OPL2/OPL3 FM: AdLib, Sound Blaster | no |
| libOPNMIDI | YM2612/YM2608 FM: Sega Genesis, PC-98 | no |
| edmidi-render | MSX FM (OPLL) and Konami SCC | no |
| TiMidity++ | Gravis Ultrasound patches (FreePats) | no |
| Nuked-SC55 | Roland Sound Canvas | yes |
| Munt | Roland MT-32 / CM-32L | yes |

The ROM engines are optional, and the cloud fleet never runs them. Put one complete ROM set per
folder, named after the variant: `roms/sc55-mk2/`, `roms/mt32/`, `roms/cm32l/`. ROMs never go in
git or in the image.

## Build the image

```bash
docker build -t sfr-render render/
docker build -t sfr-render --build-arg WITH_ROM_ENGINES=1 render/   # with ROM engines
```

The `sfr` code is copied into the image. Rebuild after you change it.

## Render one song locally

Run from the repo root. You need `soundfonts/`, the song files in `songs/` (see
[`songs/`](../songs/README.md)), and at least 100 GB free under `work/`.

```bash
sfr() { docker run --rm --user "$(id -u):$(id -g)" \
  -v "$PWD/soundfonts:/fonts:ro" -v "$PWD/songs:/songs:ro" -v "$PWD/catalog:/catalog:ro" \
  -v "$PWD/roms:/roms:ro" -v "$PWD/work:/work" -v "$PWD/out:/out" \
  -v "$PWD/render/engines.json:/opt/engines.json:ro" -e SFR_ENGINES=/opt/engines.json \
  sfr-render sfr "$@"; }
```

```bash
sfr doctor                                  # check tools and paths
sfr plan --song freedoom-e1m1 -v            # what would run
sfr render --song freedoom-e1m1 --limit 5   # quick smoke test
sfr render --song freedoom-e1m1             # every variant
sfr retry-failed --song freedoom-e1m1
sfr manifest --song freedoom-e1m1           # validate, pack, write out/public/
```

Some failed jobs are expected. A variant that comes out silent or too loud is simply left out. Use
`sfr manifest` to judge whether a song is complete: exit code 3 means planned variants are missing.

## Render many songs locally

`full-run.sh` renders, manifests and publishes one song at a time, so each song goes live as soon
as it is done. It is safe to restart. Run it in tmux.

```bash
render/scripts/full-run.sh freedoom-map01 freedoom-e1m1
render/scripts/full-run.sh --all
```

## Publishing

Publishing uploads `out/public/` to the site bucket and refreshes `songs.json`. Every call is real
and billed, so always dry-run first.

```bash
cd render && python3 -m sfr publish --dry-run
cd render && python3 -m sfr publish
```

**Careful:** your local `out/public/` only has the songs rendered on this machine. A local publish
can drop songs that were rendered in the cloud from `songs.json`, and `--prune` can delete their
audio. If the cloud fleet has ever published, use the admin box's **Published** tab instead.

## Cloud runs

The fleet is a Spot AWS Batch queue that sits at zero until a run starts. Normally you start runs
from the admin box's **Renders** tab. To start one from your machine instead:

Push the image, since the fleet uses `:latest`:

```bash
REPO=$(jq -r .render_fleet.value.ecr_repository_url infra/live/outputs.json)
aws ecr get-login-password | docker login --username AWS --password-stdin ${REPO%%/*}
docker tag sfr-render $REPO:latest && docker push $REPO:latest
```

Upload the SoundFonts:

```bash
FONTS=$(jq -r .render_fleet.value.fonts_bucket infra/live/outputs.json)
aws s3 sync soundfonts/ s3://$FONTS/soundfonts/
```

Then submit:

```bash
python3 render/cloud/submit.py --all --shards 8 --dry-run   # cost and capacity estimate
python3 render/cloud/submit.py --song freedoom-e1m1
```

`submit.py` turns the fleet off again when it exits, even on Ctrl-C. A run that would cost more
than `--max-usd` (default $60) is refused. The shards publish audio directly, but `songs.json` is
not updated until you click **Republish** on the admin box.

## What forces a re-render

A render is cached by a hash of everything that shapes its sound: the song, the variant, the
engine version and flags, timing offsets, and the loudness settings. Changing any of these in
`engines.json` re-renders every affected variant. So does changing a SoundFont's bytes or a ROM.
Plan for it.

## Timing calibration

Engines start their audio at slightly different times. `calibrate` measures each engine against
FluidSynth, and `--write` saves the offsets to `engines.json`. For `--write`, drop the `:ro` on the
`engines.json` mount.

```bash
sfr calibrate --rep fluidsynth=sf2-2ef5bd3eb3 --rep adlmidi=adl-b58 --rep opnmidi=opn-xg
```

## Extra FM banks

`banks/wopn/` and `banks/wopl/` are copied into the image at `/opt/banks/`. Every bank needs its
licence file next to it.

To add a WOPN bank:

1. Drop the bank and its licence into `banks/wopn/`.
2. Add an entry to `catalog/opn_banks.json`.
3. Rebuild the image and the catalog (see [`catalog/`](../catalog/README.md)).

Extra WOPL banks are planned but not wired up yet.

## edmidi-render

libEDMIDI emulates the MSX OPLL FM chip and the Konami SCC wavetable chip, but has no API for
picking one. `edmidi-render/` is a small C front end that renders `opll`, `scc` or `all` (both
layered) by silencing the chip you didn't ask for. The library has no PSG voice, so there is no
`edm-psg` variant.
