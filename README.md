# Soundfont Explorer

Hear one MIDI song played by hundreds of SoundFonts and old synth chips, and switch between them
mid-phrase with no gap. Every song × sound pairing is rendered ahead of time, so the website is just
static files.

## How the pieces fit

```
admin library (MIDI files)
   └─ songs/      canonical MIDIs + songs.json
catalog/          every "variant": a SoundFont, FM bank or ROM synth
   └─ render/     song × variant → loudness-matched Opus audio + manifests
        └─ S3 + CloudFront (infra/)
             └─ web/   the player in your browser
admin/            a small web app that drives all of the above
```

## Directory map

| Directory | What it holds |
|---|---|
| [`web/`](web/README.md) | The player website (TypeScript, Vite) |
| [`render/`](render/README.md) | The render image and the `sfr` pipeline |
| [`catalog/`](catalog/README.md) | Builds the list of variants from SoundFonts and FM banks |
| [`songs/`](songs/README.md) | Tools that turn library MIDIs into the site's songs |
| [`admin/`](admin/README.md) | The admin box: library, render runs, publishing |
| [`infra/`](infra/README.md) | Terraform for AWS |
| [`scripts/`](scripts/README.md) | Links your private deployment files into the repo |
| [`.github/workflows/`](.github/workflows/README.md) | CI |

## Quick start

Run all the tests, like CI does:

```bash
for d in catalog songs/tools; do python3 -m unittest discover -s $d/tests -t . ; done
(cd web && npm ci && npm test -- --run)
```

Run the player against the small fixture site (three songs, no audio):

```bash
python3 web/test/proto/serve.py --root web/test/fixtures/site --port 8000
```

```bash
cd web && npm run dev
```

Then open http://localhost:5173/. See [`web/README.md`](web/README.md) for more.

## Deploying your own

This repo has no account ids, domains or emails. They live in a private "overlay" repo that you
link in with [`scripts/overlay.sh`](scripts/README.md). After that, follow
[`infra/README.md`](infra/README.md) and then [`admin/README.md`](admin/README.md).

## What is not in git

SoundFonts, ROMs and song MIDIs are large and carry their own licences, so they stay out of git.
`songs/licenses.json` and `songs/LICENSES/` hold the song licence notices. Font and engine licences
are listed in the catalog and on the site's credits page. See [`LICENSE`](LICENSE) for the code.
