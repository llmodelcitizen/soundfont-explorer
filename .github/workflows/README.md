# CI

Each workflow runs on pushes to `main` and on pull requests, but only when files it checks have
changed. Markdown-only changes run nothing. The workflows only check the code. They never deploy
anything.

| Workflow | What it checks | Runs when these change |
|---|---|---|
| `python.yml` | Unit tests for catalog, songs, sfr, the admin server and both Lambdas (Python 3.12) | `catalog/`, `songs/`, `render/`, `admin/{server,scripts,systemd}/`, the Lambdas, `web/test/proto/` |
| `web.yml` | Typecheck, unit tests and build of the player (Node 22) | `web/`, `admin/web/src/storage.ts` |
| `admin-web.yml` | The same for the admin page | `admin/web/` |
| `lint.yml` | `shellcheck` on shell scripts, `hadolint` on `render/Dockerfile` | shell scripts, `render/Dockerfile` |
| `terraform.yml` | `terraform fmt` and `validate` on `infra/` (Terraform 1.15.9) | `infra/` |

Editing a workflow file always runs that workflow. If you add a test that reads files from another
folder, add that folder to the workflow's `paths` list too, or the test won't run when that folder
changes.

Browser checks (smoke, geometry, screenshots) are not run in CI. Run them by hand (see
[`web/`](../../web/README.md)).

## Run the same checks locally

From the repo root.

Python:

```bash
python3 -m unittest discover -s catalog/tests -t . -v
python3 -m unittest discover -s songs/tools/tests -t . -v
python3 -m unittest discover -s render/sfr/tests -t render -v
python3 -m unittest discover -s render/scripts/derisk/tests -t render/scripts/derisk -v
python3 -m unittest discover -s web/test/proto -p 'test_*.py' -v
python3 -m unittest discover -s admin/server/tests -t admin/server -v
python3 -m unittest discover -s infra/modules/circuit-breaker/lambda/tests -t infra/modules/circuit-breaker/lambda -v
python3 -m unittest discover -s infra/modules/render-fleet/lambda/tests -t infra/modules/render-fleet/lambda -v
```

Web and admin web:

```bash
(cd web && npm ci && npm run typecheck && npm test -- --run && npm run build)
(cd admin/web && npm ci && npm run typecheck && npm test -- --run && npm run build)
```

Lint:

```bash
shellcheck $(git ls-files '*.sh') admin/scripts/sfadmin-update render/scripts/sfr
docker run --rm -i hadolint/hadolint hadolint --ignore DL3008 --ignore DL3003 \
  --ignore DL3013 --ignore DL3006 - < render/Dockerfile
```

Terraform:

```bash
terraform fmt -check -recursive infra
for d in infra/bootstrap infra/live; do
  terraform -chdir=$d init -backend=false -input=false >/dev/null && terraform -chdir=$d validate
done
```

The Python tests need nothing beyond the standard library. Some sfr tests are skipped if `opusdec` or a local `soundfonts/` folder is missing.
