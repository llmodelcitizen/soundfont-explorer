# admin

A small, admittedly janky web app that allows the site owner to:

- manage the MIDI library (**Library** tab)
- start render runs on the cloud fleet (**Renders** tab)
- remove or republish songs on the live site (**Published** tab)

It lives on a throwaway EC2 box that you start when you need
it and stop when you're done. Log in with GitHub.

## Layout

| Path | What it is |
|---|---|
| `server/` | The FastAPI app (`sfadmin`) |
| `web/` | The admin web page (TypeScript, Vite) |
| `scripts/` | Run from your machine: `deploy.sh`, `up.sh`, `down.sh`, `logs.sh`. Run on the box: `bootstrap.sh`, `sfadmin-update`. |
| `systemd/`, `caddy/` | Service files and the HTTPS proxy config for the box |

The box keeps nothing important on its own disk. Everything lives in the admin S3 bucket: the
library, render runs, the app bundle and the TLS certificates. At boot the box pulls all of it
down.

You need: an authenticated AWS CLI, the
[Session Manager plugin](https://docs.aws.amazon.com/systems-manager/latest/userguide/session-manager-working-with-install-plugin.html),
`npm`, `pip3`, and `enable_admin = true` applied (see [`infra/`](../infra/README.md)).

## One-time setup

1. Create a GitHub OAuth app with the callback URL `https://admin.<your-domain>/auth/callback`.
2. Store four secrets in SSM. `allowed_emails` is a comma-separated list of verified GitHub emails.

   ```bash
   P=/soundfont-explorer/admin
   aws ssm put-parameter --type SecureString --name $P/github_client_id     --value '<client id>'
   aws ssm put-parameter --type SecureString --name $P/github_client_secret --value '<client secret>'
   aws ssm put-parameter --type SecureString --name $P/session_key          --value "$(openssl rand -hex 32)"
   aws ssm put-parameter --type SecureString --name $P/allowed_emails       --value 'you@example.org'
   ```

3. Upload a General MIDI SoundFont for in-browser previews. Without it, previews are off.

   ```bash
   BUCKET=$(jq -r .admin.value.bucket infra/live/outputs.json)
   aws s3 cp path/to/gm.sf2 s3://$BUCKET/assets/gm.sf2
   ```

4. Deploy, start the box, log in, and run a full **Canon check** (Library tab, nothing selected).
   Until you do, the render list is a 25-song stub.

## Deploy code

This uploads a new app bundle. It ships the **committed** state (`git archive HEAD`), so commit
first.

```bash
admin/scripts/deploy.sh
```

A box that boots afterwards uses the new bundle. A box that is already running needs
**Update & restart** in the page header.

Update & restart is not enough when Terraform outputs or `bootstrap.sh` changed. That includes
turning on the render fleet. In those cases, restart the box:

```bash
admin/scripts/down.sh && admin/scripts/up.sh
```

## Start and stop the box

```bash
admin/scripts/up.sh      # about 3 minutes to ready; prints the URL
admin/scripts/down.sh    # terminates it
```

Only one box runs at a time. Running it all month costs about $10, so stop it when you're done.
The **Shut down box** button does the same thing as `down.sh`.

## Watching the box

```bash
admin/scripts/logs.sh          # app, proxy, boot and update logs
admin/scripts/logs.sh --all    # the whole system journal
```

To get a shell (there is no SSH):

```bash
aws ssm start-session --target <instance-id>
```

If the wait screen says **failed**, look at the boot or update log on the box. Then retry the
update, or restart the box.

```bash
journalctl -u sfadmin-seed -u sfadmin-update -n 200
sudo systemctl start sfadmin-update
```

## Revoking access

Edit the list, or rotate the session key to log everyone out. Either change takes effect within
about a minute, with no restart.

```bash
aws ssm put-parameter --overwrite --type SecureString \
  --name /soundfont-explorer/admin/allowed_emails --value 'a@example.org,b@example.org'
aws ssm put-parameter --overwrite --type SecureString \
  --name /soundfont-explorer/admin/session_key --value "$(openssl rand -hex 32)"
```

## Undo and safety

- Library changes are versioned in S3 for 90 days. To undo one, restore the older object version.
- Only one render run can be live at a time. While it runs, Remove, Prune and Republish are locked.
- In the Renders tab, click **Estimate** before you submit. A run that costs more than $60 is
  refused.
- **Remove** deletes a song's audio from the site for good.

## One-time migration scripts

`scripts/ingest.py` and `scripts/seed_library.py` created the first library from the old
in-repo song folders. You should not need them again. Both take `--dry-run`, and both refuse to
overwrite an existing library unless you force them.
