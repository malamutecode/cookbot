---
name: deploy-release
description: Ship a release to Cloud Run + Firebase Hosting safely — pre-flight checks, correct backend/frontend ordering, post-deploy verification, and the rollback command ready before it's needed. Wraps infra/deploy-backend.sh and infra/deploy-frontend.sh rather than hand-rolling gcloud. Use when the user says "deploy", "ship it", "release", "push to prod", "roll back", or asks whether a change is safe to deploy.
---

# deploy-release

Get a verified change into production without the two failure modes this setup
actually has: **deploying a broken build**, and **deploying the halves out of
order so the WebSocket silently breaks**.

The scripts in `infra/` already do the mechanical work well — they load config,
guard the service/hosting mismatch, smoke-test `/health`, and print the rollback
command. **Never hand-roll `gcloud` or `firebase` commands**; this skill decides
*whether*, *in what order*, and *verified how* — it does not reimplement them.

There is no CI in this repo (no `.github/workflows/`), so every gate that would
normally be automated has to happen here, deliberately, before the push.

---

## Step 0 — Authorization

Deploying is outward-facing and hard to reverse. **Confirm with the user before
any non-dry-run deploy**, and state what will change: which service, which
target(s), and whether config values move.

Approval for one deploy does not carry to the next. `--dry-run` needs no approval
— prefer it whenever the user is asking "would this work?" rather than "ship it".

---

## Step 1 — Pre-flight: is this change deployable?

Run these before touching a deploy script. Any failure stops the release.

```bash
git status --short          # uncommitted work
git log --oneline -5        # what is actually about to ship
git diff --stat origin/main HEAD 2>/dev/null || true
```

| Check | Why it blocks |
|---|---|
| **Working tree clean** | Cloud Build ships the *build context*, not HEAD — uncommitted files deploy silently and untracked ones don't. Commit or stash first. |
| **Tests are green** | Nothing here runs them for you. If `pre-commit-check` hasn't run against this diff, run it now — that skill picks the tier. |
| **Diff reviewed** | Know which STEP or fix is shipping; you need it for the verification step and the rollback decision. |
| **Config drift** | New/changed env var or `TenantConfig` field ⇒ `infra/deploy.env` and Secret Manager must have it, or the container boots and fails on first request. |

The config check is the one most often missed. When the diff adds a setting:

```bash
grep -n "NEW_VAR" .env.example infra/deploy.env.example infra/deploy-backend.sh
```

A variable in `.env.example` but absent from `deploy-backend.sh`'s `SUBS` block
and `cloudbuild.yaml` will **not** reach Cloud Run. Adding it is part of the
change, not a follow-up.

---

## Step 2 — Decide the targets, and get the order right

| Diff touches | Deploy |
|---|---|
| `packages/**`, `clients/tastyhub/app/**`, `Dockerfile`, `cloudbuild.yaml` | **backend** |
| `frontend/src/**`, `frontend/index.html`, Vite/Tailwind config | **frontend** |
| Both | **backend first, then frontend** |
| Only `*.md`, `tests/**`, `.claude/**` | **nothing** — say so and stop |

**Order is load-bearing.** The frontend build inlines `VITE_WS_BASE` at compile
time. If the Cloud Run hostname changes (new or recreated service), a frontend
built against the old value connects to a host that no longer exists — and it
fails at WebSocket-open, not at build. Backend first means the frontend is always
built against a hostname that already exists.

`deploy-backend.sh` detects this drift for you: after deploying it compares the
live URL to `frontend/.env.production` and warns. **Treat that warning as a
required action** — update the file and deploy the frontend, or the app is broken
for users even though both deploys "succeeded".

---

## Step 3 — Dry run first

For any release that changes config, is the first in a while, or touches infra:

```bash
./infra/deploy-backend.sh --dry-run
```

Read the printed config block back to the user — especially these two, which the
script warns about because they are security-relevant:

- `ALLOWED_EMAILS` empty ⇒ **any** Firebase account can sign in.
- `ALLOWED_ORIGINS=*` ⇒ fine for a smoke test, wrong for prod.

If either warning appears on a real production deploy, raise it before proceeding.
The user may accept it deliberately — say it once, then respect the answer.

---

## Step 4 — Deploy

```bash
# Backend — Cloud Build → Artifact Registry → Cloud Run, ~3-6 min, includes /health smoke
./infra/deploy-backend.sh

# Frontend — npm build → Firebase Hosting. Use --ci for a lockfile-exact install.
./infra/deploy-frontend.sh --ci
```

These are long-running. Prefer `run_in_background: true` and report when they
finish rather than blocking, but **do not start the frontend deploy until the
backend one has actually succeeded** — the ordering in Step 2 only helps if it's
sequential.

Never pass `--no-smoke` on a real release. It exists for debugging a known-broken
health endpoint, not for saving 30 seconds.

`bootstrap.sh` is **one-time project setup**, not part of a release. Do not run it
during a deploy, even if a resource looks missing — that is a separate,
explicitly-requested act.

---

## Step 5 — Verify beyond /health

The built-in smoke test only proves the container boots. `/health` returns
`{"status": "ok"}` from a process that may still be unable to reach Firestore,
OpenAI, or authenticate anyone.

```bash
# What the script already did — confirm it passed, don't repeat it blindly
curl -fsS "$URL/health"

# Recent logs: look for boot-time config errors, not just 500s
gcloud run services logs read "$SERVICE" --region="$REGION" --limit=50
```

Then verify **the thing that shipped**. Match the check to the diff:

- Agent/chat change → open a real chat turn and exercise the changed path.
- REST/admin change → hit the route with a real token.
- Frontend change → load the Hosting URL, confirm the WebSocket connects (that is
  the `VITE_WS_BASE` check in practice).

Report what you verified and what you did not. "Deployed and healthy" without a
functional check is an overstatement — say "deployed, `/health` green, chat turn
not exercised" when that's the truth.

---

## Step 6 — Rollback

Know this before you need it. `deploy-backend.sh` prints it at the end:

```bash
gcloud run revisions list --service="$SERVICE" --region="$REGION"
gcloud run services update-traffic "$SERVICE" --region="$REGION" \
  --to-revisions=<PREVIOUS_REVISION>=100
```

Cloud Run keeps revisions, so rollback is traffic-shifting — fast and safe.
**Firebase Hosting rolls back separately** through its release history; a backend
rollback alone leaves a newer frontend talking to an older API. If the frontend
shipped in the same release, roll both back.

Rolling back is a production change like any other: tell the user what you're
doing and why, and confirm unless they've already said "roll it back".

---

## Notes specific to this repo

- **Build context is the repo root.** `clients/tastyhub/uv.lock` pins editable
  path deps at `../../packages/...`, so the Dockerfile COPYs from the root. Never
  "simplify" the build to the client directory.
- **`--labels client_id={name}`** on the Cloud Run service drives cost attribution.
  `deploy-backend.sh` sets it; preserve it in any manual override.
- **`check_service_matches_hosting`** guards the `firebase.json` rewrite's
  `serviceId` against `$SERVICE`. If it fires, the two are genuinely out of sync —
  fix the config, don't bypass the check.
- **Secrets come from Secret Manager** via `--set-secrets`. Never bake one into an
  image, an env var in `cloudbuild.yaml`, or a commit.
- **`infra/deploy.env` is gitignored.** If it's missing, the script dies with
  instructions; copy from `deploy.env.example` and fill it in — don't invent values.
