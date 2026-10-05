# web

The player. Pick a song and move up and down the list of variants, and the music switches sound
instantly, at the same point in the song. You can also:

- hold ↓ to scrub through fonts
- pin one sound with `P` and flip back to it with `Tab` (A/B)
- filter by engine, chip, era, size and more
- switch themes: modern, Windows 95, Amiga

Press `?` in the app for all keys.

It is a static site with no backend. It reads three kinds of JSON and the audio from its own
domain:

```
/songs.json            the song list (short cache)
/c/<hash>.json         the catalog of variants
/s/<song>/<hash>.json  one song's layout: which audio files exist
/a/<song>/…            audio: packed scrub slices (.pk) and listen slices (.opus)
```

The render pipeline makes and publishes all of that (see [`render/`](../render/README.md)). This
folder only builds the page and its code.

## Run it locally

You need Node 22. You also need a data server, and it must support HTTP Range requests, because
the player reads parts of audio packs. Use `test/proto/serve.py`. `python3 -m http.server` will
**not** work.

Against the small fixture site (three songs, no audio):

```bash
python3 web/test/proto/serve.py --root web/test/fixtures/site --port 8000
```

Against your own renders:

```bash
python3 web/test/proto/serve.py --root out/public --port 8000
```

Then, in another shell:

```bash
cd web && npm ci && npm run dev    # http://localhost:5173
```

To use a different data server, set `SFP_DATA_ORIGIN=http://host:port` before `npm run dev`.

## Test and build

```bash
npm test -- --run       # unit tests (plain `npm test` stays in watch mode)
npm run typecheck
npm run build           # → web/dist/
```

`src/state/storage.ts` must stay identical to `admin/web/src/storage.ts`. A test checks this.

## Browser checks

These need the dev server running. They use Playwright, so install the browser once:

```bash
npx playwright install chromium
```

```bash
node scripts/smoke.mjs                      # play, scrub, themes, deep links
npm run test:geometry -- --audio=none       # layout checks (use against fixtures)
node scripts/screenshots.mjs                # every theme → ../work/shots
node scripts/mobile-check.mjs <site-url>    # iPhone layout report
```

## Deploy

Deploying uploads only the page and its code. Songs and audio are published separately (see
[`render/`](../render/README.md)). You need the overlay files linked (see
[`scripts/`](../scripts/README.md)). `web/.env.local` sets the takedown contact shown on the About
page.

```bash
web/scripts/deploy.sh --dry-run
web/scripts/deploy.sh
```

Code files are cached for a year, because their names change on every build. `index.html` is cached
for 60 seconds.

## `test/proto`

These are early browser experiments for decoding, A/B switching and fetching packs. They are kept
for history. Two parts are still in use: `serve.py`, and the pack test that CI runs.
