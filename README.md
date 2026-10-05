# Eric's Soundfont Explorer

Live at **https://soundfonts.ericq.com/** — 238 songs × ~560 variants ≈ 134,000 renders, served
as a static site using S3 & CloudFront. Hold ↓ and the list scrubs instantaneously; the music never stops!

**One MIDI, every synth.** Hear the same piece through nearly 500 SoundFonts and a shelf of emulated
sound chips — OPL3/OPL2 (libADLMIDI, plus ESFM and CQM clone passes), OPN2/OPNA (libOPNMIDI),
OPLL/SCC (libEDMIDI), Gravis Ultrasound patches (TiMidity++/FreePats), and, with owner-supplied
ROMs, Roland SC-55 (Nuked-SC55) and MT-32/CM-32L (Munt) — switching timbre instantly while the
music keeps playing.

## How it works

Every variant is pre-rendered offline and loudness-matched: EBU R128 to −16 LUFS with a −1.5 dBTP
ceiling, using pure linear gain, with reverb and chorus switched off wherever the synth allows.
Each render is resampled to 48 kHz and encoded as Opus in two tiers:

- 2 s "scrub" segments at 48 kbps, packed 24 variants per object so neighbours arrive together
- 10 s "listen" segments at 96 kbps that crossfade in once you settle on a variant

Each scrub segment carries a 120 ms lead-in and overlaps the next one by 20 ms of bit-identical
audio, so seams and switches are short, sample-aligned crossfades on a single Web Audio timeline.
A song is rendered once per variant, then cached by a hash of everything that shapes its sound.

```
admin library (MIDI) ─► songs/ canon ─┐
                                      ├─► render/ sfr (Docker, every engine pinned)
catalog/ variants.json ───────────────┘        │  render → loudness → Opus → packs + manifests
                                               ▼
                               S3 + CloudFront (infra/) ─► web/ player
```

| Directory | What |
|---|---|
| [`web/`](web/README.md) | TypeScript + Vite client: Web Audio scheduling, adaptive ↑/↓ policy, facets, themes (modern, Windows 95, Amiga Workbench 1.3) |
| [`catalog/`](catalog/README.md) | SoundFont scanner, facets, FM-bank catalog, `variants.json` (the stable id space) |
| [`songs/`](songs/README.md) | The canonicalizer and the licence table. Every song is a file in the admin library; the MIDIs themselves are not in the repository. |
| [`render/`](render/README.md) | `Dockerfile` with every engine pinned; the `sfr` pipeline: `render → manifest → publish`; cloud runs on AWS Batch Spot |
| [`admin/`](admin/README.md) | Throwaway EC2 admin app: MIDI library, render runs, publishing; GitHub login |
| [`infra/`](infra/README.md) | Terraform: S3 + OAC + CloudFront (HTTP/2+3), ACM, Route 53, budgets, egress circuit breaker, render fleet, admin box |
| [`scripts/`](scripts/README.md) | `overlay.sh`: links the private deployment files into the checkout |
| [`.github/workflows/`](.github/workflows/README.md) | CI: unit tests, lint, Terraform validate |

## A score, not a sound

A MIDI file is not a recording. It's a score: *note 60 on, channel 1, velocity 100, program 0.*
What you actually hear depends entirely on the box that reads it. For about fifteen years, that box
was different on every desk.

**1983 — MIDI.** Dave Smith (Sequential Circuits) and Ikutaro Kakehashi (Roland) get rival
synths talking over a 5-pin cable. A Prophet-600 plays a Roland Jupiter-6 at the Winter NAMM show,
and the spec ships that August. Both men later share a Technical Grammy for it. MIDI says *what*
to play, never *how it should sound*.

**1987 — the PC learns to sing.** The AdLib card puts a Yamaha YM3812 (OPL2) in your PC: nine
voices of two-operator FM, the same family of math as the DX7, squeezed onto one cheap chip. In
1988 Sierra ships *King's Quest IV* with an AdLib soundtrack, and the Sound Blaster (1989) clones
the chip and buries the AdLib. Later cards move up to the OPL3 and four-operator voices. A decade
of DOS music is FM. That's the buzzy, glassy, unmistakable sound of *Doom*, *Duke Nukem*
and *Commander Keen* on a Sound Blaster.

**1987 — the rich kid's option.** Roland's MT-32 brings LA synthesis (sampled attacks plus
synthesized sustains) and studio reverb to the desktop. Sierra games written for it sound like a
different universe. Its instrument map is Roland's own, not
the later GM standard.

**Meanwhile, on consoles.** Yamaha FM is everywhere. The YM2612 (OPN2) gives the Sega Mega
Drive/Genesis its snarling bass (1988). The YM2608 (OPNA) powers NEC's PC-98 in Japan. The
YM2413 (OPLL) is a stripped-down FM chip with fifteen preset instruments, used in MSX-MUSIC. And
Konami builds its own wavetable chip, the SCC, into game cartridges, starting with *Nemesis 2*
(1987).

**1991 — General MIDI.** The industry finally agrees that program 0 is a piano, program 25 a steel
guitar, and channel 10 the drums. Roland's SC-55 Sound Canvas is the first GM module, adds its
own "GS" extensions, and becomes the sound every 90s composer actually wrote for.

**1992 — samples for the masses.** The Gravis UltraSound plays real recorded samples ("patches")
from on-board RAM. Demosceners and tracker musicians love it.

**1994 — SoundFonts.** E-mu and Creative ship the Sound Blaster AWE32 with the EMU8000 chip,
and a file format that lets *anyone* load their own sampled instrument set into the card. SoundFont
2.0 (1996) makes it an open spec. A generation of hobbyists rips, records and trades sets: Roland
clones, Yamaha XG imitations, console-flavoured kits, orchestral monsters, beautiful oddities.
Software synths like FluidSynth keep them alive long after the cards are gone.

So the same `.mid` file has hundreds of "correct" sounds, and there has never been an easy way to
hear them side by side, mid-phrase. That's what Soundfont Explorer sets out to do.

| Source | What | Variants |
|---|---|---|
| [**500 Soundfonts Collection**](https://archive.org/details/500-soundfonts-full-gm-sets), uploaded by DoomFanatic | 500 GM-compatible SF2 files from the early 90s to 2022 | 495 |
| libADLMIDI's embedded banks | 79 OPL instrument banks lifted from DOS games and drivers (AIL, DMX, HMI, Apogee, Fat Man…), on Nuked OPL3; plus OPL2, ESFM and CQM passes | 126 |
| libOPNMIDI's `fm_banks/` | 7 OPN banks, each on YM2612 and YM2608 | 14 |
| libEDMIDI | MSX OPLL, Konami SCC, and both layered | 3 |
| TiMidity++ + FreePats | Gravis-style patch set | 1 |
| Nuked-SC55, Munt | Roland SC-55 family and MT-32/CM-32L. Need ROMs you own; not on the live site. | 11 |

The catalog keeps per-file provenance (`catalog/collections.json`), and the player shows each
font's own SF2 metadata and licence notes. Song licences (Freedoom BSD-3, public domain, CC0, or
owner-supplied) and their notices live in [`songs/`](songs/README.md).

## Keys

`↑/↓` variant (hold to scrub) · `PgUp/PgDn` ±10 · `Home/End` · `Space` play/pause · `←/→` ±5 s
(`Shift` ±30 s) · `X` stop · `L` loop · `M` mute · `V` favorite/unfavorite · `/` search ·
`Esc` close/clear · `[` `]` song · `P` pin A, `Tab` A/B · `F` filters · `Shift + F` full screen ·
`T` theme · `S` settings · `D` debug panel · `?` keymap.

## Run it

All the tests, the same way CI runs them: see [`.github/workflows/`](.github/workflows/README.md).
To run the player against the three-song fixture site (no audio):

```bash
python3 web/test/proto/serve.py --root web/test/fixtures/site --port 8000
```

```bash
cd web && npm ci && npm run dev    # http://localhost:5173
```

To deploy your own: link your private overlay with [`scripts/overlay.sh`](scripts/README.md),
then follow [`infra/`](infra/README.md) and [`admin/`](admin/README.md).

## Licences

The code is under [`LICENSE`](LICENSE). SoundFonts, ROMs and song MIDIs are not in git and carry
their own terms. Song notices live in `songs/licenses.json` and `songs/LICENSES/`. Engine and
font licences are listed in the catalog and on the site's credits page.
