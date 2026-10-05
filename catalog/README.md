# catalog

The catalog is the list of every sound the site can play. Each entry is a **variant**: one
SoundFont, one FM bank on one chip, or one ROM synth. Today there are 650 of them: 495
SoundFonts, 143 FM chip variants, and 12 other synths (Gravis, Sound Canvas, MT-32). The render
pipeline and the website both read `variants.json`.

Every tool here uses only the Python standard library. Run them from the repo root.

## Rebuild order

Each step reads the output of the step before it. You only need to rerun from the step whose input
changed.

```bash
python3 -m catalog.sf2scan      # soundfonts/*.sf2 → soundfonts.json (headers + sha256)
python3 -m catalog.build        # + overrides.json → soundfonts.facets.json
python3 -m catalog.adlbanks     # asks the sfr-render image → adl_banks.json
python3 -m catalog.variants     # everything → variants.json + review.md
```

`provenance` is optional. It records which download a font came from:

```bash
python3 -m catalog.provenance --zip soundfonts/<collection>.zip --id <id> --title "<title>" --url <url>
```

## Files

| File | Made by | Edit by hand? |
|---|---|---|
| `soundfonts.json` | `sf2scan`: one record per font file (name, size, sha256, SF2 INFO, presets) | no |
| `soundfonts.facets.json` | `build`: adds facets and duplicate groups | no |
| `adl_banks.json` | `adlbanks`: the OPL banks built into libADLMIDI | no |
| `collections.json` | `provenance`: where fonts came from | no |
| `variants.json` | `variants`: the final list, in canonical order | no |
| `review.md` | `variants`: a human-readable licence and decisions report (not committed) | no |
| `overrides.json` | — | **yes**: fixes facets for single fonts |
| `adl_passes.json` | — | **yes**: which extra OPL chip passes to render |
| `opn_banks.json` | — | **yes**: the OPN banks and their licences |

## Overrides

`overrides.json` fixes a font's facets when the automatic rules get them wrong. Key each font by
its sha256 (this survives renames) or by its exact file name:

```json
"<sha256>": { "file": "Some Font.sf2", "lineage": "roland", "publish": false, "notes": "why" }
```

Allowed keys are `lineage`, `completeness`, `bank_map`, `instrument`, `year`, `decade`,
`license_flag`, `publish`, `label` and `notes`. The values must come from these lists:

| Facet | Values |
|---|---|
| `completeness` | `full_gm`, `melodic_only`, `drums_only`, `partial`, `single_instrument` |
| `bank_map` | `gm`, `gs_var`, `xg`, `gm2`, `multi`, `drums_only`, `melodic_only` |
| `lineage` | `console`, `fm_sampled`, `gravis`, `roland`, `yamaha_xg`, `creative`, `gs_compat`, `generic` |
| `license_flag` | `roland_copyright`, `gpl`, `public_domain`, `free`, `unknown` |

`publish` must be `true` or `false`. A font with `publish: false` is skipped by the renderer.

After editing, rerun `build` and `variants`.

## Things to know

- A SoundFont variant's id comes from the file's hash. If you change the file's bytes, it becomes
  a new variant and must be rendered again.
- `adlbanks` expects exactly 79 banks. Any other number means the render image changed.
- The SF2 comment field (`ICMT`) can contain personal data. It is used for facet guessing, but it is
  never shown on the site.
