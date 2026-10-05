# songs

Tools that turn the admin library's MIDI files into the songs the site plays. The music itself is
not in git. Only the tools and the licence notices are.

## How a MIDI becomes a song

```
admin library (library.json + FILES/)
   └─ tools/fragment.py → corpus-imports.json   the song list (hidden entries left out)
        └─ tools/canon.py  → rendered/*.mid        clean, deterministic MIDI files
                           → songs.json            ids, titles, lengths, licences
```

`canon.py` cleans each file the same way every time. It can trim, add instrument changes, and add
an end marker. It then works out the song's length and attaches its licence. The renderer and the
website read what it writes.

The admin box runs this for you when you click **Canon check** in the Library tab. You only need
the commands below on your own machine.

## Get the current songs

You don't have to rebuild anything. Copy the admin box's last results from S3:

```bash
BUCKET=$(jq -r .admin.value.bucket infra/live/outputs.json)
aws s3 sync s3://$BUCKET/canon/rendered/ songs/rendered/ --size-only
aws s3 cp s3://$BUCKET/canon/songs.json songs/songs.json
aws s3 cp s3://$BUCKET/canon/corpus-imports.json songs/corpus-imports.json
aws s3 sync s3://$BUCKET/library/FILES/ songs/import/FILES/ --size-only
```

## Rebuild

```bash
aws s3 cp s3://$BUCKET/library/library.json work/library.json
python3 songs/tools/fragment.py --library work/library.json
python3 songs/tools/canon.py              # rebuild everything
python3 songs/tools/canon.py --check      # fail if songs.json would change
python3 songs/tools/canon.py --lenient    # list bad files in canon-report.json instead of stopping
python3 songs/tools/canon.py --only <id>  # redo one song
```

## Rules

- **Never change a song's id.** Renders are stored under the id, so a new id makes all of that
  song's audio orphaned. Ids are fixed in the library, and renames keep them.
- The default song must exist, or `canon.py` stops.
- Every song needs a `license` key that is in `licenses.json`. An unknown key is refused.

## Adding a licence

Add an entry to `licenses.json`. Put the notice text inline, or as a file in `LICENSES/` that the
entry points to with `notice_file`. The site's credits page shows these notices, so they stay in
git.

## Small tools

```bash
python3 songs/tools/dump.py file.mid                       # tracks, channels, programs
python3 songs/tools/make_diagnostic.py out.mid             # a GM test song (all 128 programs + drums)
python3 songs/tools/inject_programs.py in.mid out.mid rules.json
```

Run the tests:

```bash
python3 -m unittest discover -s songs/tools/tests -t . -v
```
