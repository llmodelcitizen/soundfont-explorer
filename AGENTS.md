# AGENTS.md

Low-level invariants that the READMEs leave out. Read the README of the directory you are working
in first.

## Repo-wide
- Never commit account ids, domains, bucket names or emails. They live in the private overlay
  (`scripts/overlay.sh`). Use `*.example` files and placeholders.
- `web/src/state/storage.ts` and `admin/web/src/storage.ts` must stay byte-identical
  (`web/test/unit/storage.test.ts`).
- The site CSP is `connect-src 'self'` (`infra/modules/static-site`). All data must be same-origin,
  and Vite must not inline assets (`assetsInlineLimit: 0`).
- Never surface the SF2 `ICMT` chunk anywhere public. It holds third-party personal data.
  `SF2_INFO_KEYS` in `web/src/contracts/catalog.ts` is the allowlist.
- The tests are listed in `.github/workflows/ci.yml`. The Python is stdlib-only, except
  `admin/server` at runtime.

## render / catalog
- `master_hash` (`render/sfr/jobs.py`) covers the song sha, variant, engine version, commit,
  `base_args`, chips, core, start offset, drift, sample rate, D, LUFS target, peak ceiling, clamp
  and `PIPELINE_VERSION`. Touching any of these re-renders everything affected. Encode-only changes
  re-render too, unless the masters were kept.
- `render/sfr` is baked into the image, not bind-mounted. A plain `docker run` uses the image's
  `engines.json`; `full-run.sh` mounts the checkout's copy.
- Content-addressed outputs (`c/`, `s/`, `a/`) must stay deterministic: no timestamps; the Ogg
  serial comes from `render_hash`; the order of `excluded[]` matters.
- SFPK v1 is defined twice: `render/sfr/pack.py` and `web/src/audio/net/packs.ts`. Change both
  together.
- `web/src/config.ts` and `render/engines.json` share their timing numbers. `segment_samples` is
  asserted against the computed value.
- FluidSynth writes raw f32 and is killed at (D+2 s) of bytes, because a non-decaying voice renders
  forever. Keep the 8 GiB address-space limit and the check that stops it falling back to its
  default font.
- Load-bearing engine flags:
  - TiMidity: `--preserve-silence`, `-c freepats.cfg`, `-A 25`.
  - Munt: `--record-max-start-silence -1` and the long `--src-quality` flag.
  - adl/opn: write `<input>.wav` next to the input, so the MIDI is symlinked into scratch. opn
    takes options, then the bank, then the MIDI. adl treats an unknown `--emu-X` as a bank file.
- Calibration uses the 1 s and 170 s clicks, never the 0 s click (the Nuked cores start late).
- Container scratch is `/work`, never `/tmp`, which is tmpfs on the host.
- Song ids and sf2 variant ids (sha prefixes) are cache identity. Never re-mint them.
- `canon.py` refuses to run without `corpus-imports.json`; this protects a synced `songs.json`.

## admin
- Root never executes anything from the `sfadmin`-owned tree. It uses a `$ROOTSTAGE` copy, pip
  runs as `sfadmin`, and an IAM Deny covers `app/*`.
- `sfadmin-update` installs itself under a temp name and then renames it, because bash reads a
  running script lazily.
- `publocks` is a plain, non-reentrant `Lock`, so nesting it deadlocks. `OPS_LOCK` is reentrant.
- `library.json` writes are ETag-conditional. A 409 means another writer won; don't retry blindly.
- `bundle.env` reaches `/etc/sfadmin.env` only at boot (`bootstrap.sh`). `sfadmin-update` doesn't
  rewrite it.
- Caddy sync deliberately has no `--size-only`.
- SSM secrets are cached for 60 s. On a refresh failure the last value keeps being served.

## infra
- Module gates (`enable_admin`, `enable_render_fleet`) belong in `terraform.tfvars`. A missing
  gate plans a destroy.
- `static-site` ignores drift on `enabled`, because the circuit breaker owns that field.
- The watchdog terminates anything tagged `project=soundfont-explorer-render` that is older than
  4 h. The admin box has its own tag on purpose.
- The Batch compute environment is created ENABLED (AWS requires it) and then disabled by a
  `local-exec`, so `apply` needs the AWS CLI.
