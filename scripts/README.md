# scripts

## overlay.sh

This repo is public, so it holds no account ids, domains or email addresses. Those live in a
private "overlay" repo with the same folder layout. `overlay.sh` symlinks four files from it into
this checkout:

| File | Used by |
|---|---|
| `infra/live/backend.hcl` | `terraform init` |
| `infra/live/terraform.tfvars` | `terraform apply` |
| `infra/live/outputs.json` | almost every deploy and render script |
| `web/.env.local` | the web build (takedown contact) |

All four are gitignored here. Because they are symlinks, anything that writes to them, like
`terraform output -json > infra/live/outputs.json`, actually updates the overlay.

```bash
scripts/overlay.sh                                # uses $SOUNDFONT_EXPLORER_OVERLAY or ../soundfont-explorer-overlay
scripts/overlay.sh ~/src/my-overlay
```

To start your own overlay, copy `infra/live/*.example` into it at the same paths and fill them in.
Commit the overlay to a **private** repo.
