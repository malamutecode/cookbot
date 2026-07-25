# infra/ — deploy scripts

Portable bash (git bash on Windows, Linux, macOS). All config comes from
`infra/deploy.env`, which is gitignored.

## First time on this machine

```bash
cp infra/deploy.env.example infra/deploy.env
# edit it: PROJECT_ID, ADMIN_UIDS, ALLOWED_EMAILS, ...
```

## Scripts

| Script | When | What it does |
|---|---|---|
| `./infra/bootstrap.sh` | once per GCP project | Enables APIs, creates Firestore + Artifact Registry + secrets, grants IAM. Idempotent. Deploys nothing. |
| `./infra/deploy-backend.sh` | every backend release | Cloud Build → Artifact Registry → Cloud Run, then a `/health` smoke test. |
| `./infra/deploy-frontend.sh` | every frontend release | `npm run build` → Firebase Hosting. |

Backend and frontend are independent — neither requires the other.

Common flags: `--dry-run` (print commands, change nothing), `--help`.
`deploy-backend.sh --no-smoke`, `deploy-frontend.sh --ci|--skip-build`.

## Overrides

Anything already set in your shell wins over `deploy.env`, so one-offs work
without editing the file:

```bash
ALLOWED_EMAILS=someone@else.com ./infra/deploy-backend.sh
```

## Guardrails these scripts add

- **`SERVICE` vs `firebase.json`** — warns if the Cloud Run service name has no
  matching `serviceId` rewrite, which would silently break the same-origin
  `/v1` proxy.
- **`VITE_WS_BASE` vs the live service** — the frontend deploy refuses (with a
  prompt) when the baked WebSocket host doesn't match the deployed Cloud Run URL,
  and the backend deploy warns when a deploy changes that host. Vite bakes
  `VITE_*` at build time, so a stale value ships a broken app rather than failing.
- **Placeholder detection** — a `.env.production` still containing `<your-...>`
  aborts the frontend deploy.
- **Comma-separated values** — `--substitutions` always splits on `,`, so the
  scripts use the `^;^` delimiter prefix. Getting this wrong mangles
  `ALLOWED_ORIGINS` / `ALLOWED_EMAILS` / `ADMIN_UIDS`.
- **Secrets never hit disk or argv** — `bootstrap.sh` reads the OpenAI key with
  `read -s` and pipes it to `gcloud secrets create --data-file=-`. Existing
  secrets are never overwritten.

Narrative background, the manual Firebase-console steps, and rollback live in
[DEPLOY.md](../DEPLOY.md).
