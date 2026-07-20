# Feedek — Deployment Runbook

Backend → **Cloud Run**, frontend → **Firebase Hosting**. This file is the ordered
list of commands **you** run. Claude prepared the Dockerfile / cloudbuild.yaml /
firebase.json / config but does not execute deploys.

- **Part 1 — Backend → Cloud Run** (sections 0–8)
- **Part 2 — Frontend → Firebase Hosting + real Firebase Auth** (sections 9–13)

---

## 0. One-time setup — variables

Set these in your shell first. Replace the project id with your **production**
GCP project (the repo's `.env` uses `cookbot-local-dev`, which is a local-dev
project — decide whether prod reuses it or is a new project).

```bash
export PROJECT_ID=<YOUR_PROJECT_ID>          # e.g. feedek-prod
export REGION=europe-west1
export AR_REPO=feedek                         # Artifact Registry repo name
export SERVICE=cookbot              # Cloud Run service name

gcloud config set project "$PROJECT_ID"
gcloud auth login          # if not already authenticated
```

---

## 1. Enable the required APIs (one-time)

```bash
gcloud services enable \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  firebase.googleapis.com \
  identitytoolkit.googleapis.com          # Firebase Auth (used in Part 2)
```

---

## 2. Firestore database (one-time)

The app uses Firestore in **native** mode, database `(default)`. If the project
has no Firestore db yet:

```bash
gcloud firestore databases create --location="$REGION" --type=firestore-native
```

(If it already exists, this errors harmlessly — skip it.)

---

## 3. Artifact Registry repo (one-time)

```bash
gcloud artifacts repositories create "$AR_REPO" \
  --repository-format=docker \
  --location="$REGION" \
  --description="Feedek container images"
```

---

## 4. Secrets in Secret Manager (one-time; re-run to rotate)

The Cloud Run deploy mounts `OPENAI_API_KEY` and `API_KEY` from Secret Manager —
they are **never** baked into the image or set as plain env vars.

```bash
# OpenAI key — paste your key inline when you run this; do NOT commit it to this file.
printf '%s' 'sk-...your-openai-key...' | \
  gcloud secrets create openai-key --data-file=- --replication-policy=automatic

# Widget/API key (the x-api-key the frontend/widget sends). Generate a strong one:
printf '%s' "tk_live_$(openssl rand -hex 24)" | \
  gcloud secrets create api-key --data-file=- --replication-policy=automatic
```

To **update** an existing secret later:
```bash
printf '%s' 'new-value' | gcloud secrets versions add openai-key --data-file=-
```

> Note the value you set for **api-key** — the frontend must send the same value
> as `x-api-key` (configured in Part 2 via `VITE_API_KEY`).

---

## 5. Grant the Cloud Run runtime service account access (one-time)

Cloud Run runs as the Compute Engine default SA unless you specify another. It
needs: read the two secrets, use Firestore, and mint Firebase custom/ID-token
verification (Firebase Admin uses `firebaseauth.viewer` via the token-verify
public certs — no extra role needed for *verifying* ID tokens, but Firestore is).

```bash
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"

# Secrets
gcloud secrets add-iam-policy-binding openai-key \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor"
gcloud secrets add-iam-policy-binding api-key \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/secretmanager.secretAccessor"

# Firestore read/write
gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}" --role="roles/datastore.user"
```

The **Cloud Build** SA also needs permission to deploy Cloud Run and act as the
runtime SA:

```bash
BUILD_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"   # Cloud Build uses this by default now
# If using the legacy @cloudbuild SA, substitute ${PROJECT_NUMBER}@cloudbuild.gserviceaccount.com below.

gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${BUILD_SA}" --role="roles/run.admin"
gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" \
  --member="serviceAccount:${BUILD_SA}" --role="roles/iam.serviceAccountUser"
```

---

## 5b. Seed the first admin user (one-time)

`ADMIN_UIDS` takes a Firebase **uid**, not an email — and a uid only exists once
the account does. So create your admin account first, then use the printed uid in
the deploy. The seed script (`clients/tastyhub/scripts/seed_admin.py`) creates the
account with a **random temporary password** you change after first login.

**Prereq — enable the sign-in provider:** Firebase console → **Authentication →
Sign-in method → Email/Password → Enable**. (Without it, `create_user` fails.)

```bash
# Authenticate as yourself so the script's firebase-admin uses your credentials.
gcloud auth application-default login
export GOOGLE_CLOUD_PROJECT="$PROJECT_ID"

cd clients/tastyhub
uv run python scripts/seed_admin.py                     # defaults to pawe213@gmail.com
# or: uv run python scripts/seed_admin.py --email you@example.com
# or: uv run python scripts/seed_admin.py --password 'ChosenPass123'
cd ../..
```

The script prints:
- **uid** → paste into `_ADMIN_UIDS` in step 6.
- **temporary password** (only for a newly created account) → **save it now**, it
  is not stored anywhere else. Log in with it, then change it via your app's reset
  flow or the Firebase console (Authentication → user → ⋮ → Reset password).

It is **idempotent**: re-running on an existing account only prints the uid and
never touches the password.

```bash
export ADMIN_UID="<uid printed by the script>"
```

---

## 6. Deploy (repeat for every release)

Run from the **repo root** (the build context must be the root — the client's
`uv.lock` pins editable path deps at `../../packages/...`).

```bash
gcloud builds submit --config clients/tastyhub/cloudbuild.yaml \
  --substitutions="^;^_REGION=$REGION;_AR_REPO=$AR_REPO;_SERVICE=$SERVICE;_ALLOWED_ORIGINS=https://feedek.web.app,https://feedek.firebaseapp.com;_ALLOWED_EMAILS=pawe213@gmail.com;_ADMIN_UIDS=$ADMIN_UID;_DEFAULT_DAILY_TOKEN_LIMIT=1000000;_DEFAULT_MONTHLY_TOKEN_LIMIT=10000000"
```
> `--substitutions` always splits on `,`, so a value containing a literal comma
> (like `_ALLOWED_ORIGINS`'s two URLs) breaks the default parser no matter how
> you quote it. The `^;^` prefix switches the KEY=VALUE delimiter to `;` instead,
> so commas inside values stay intact. Keep this on one line — a backslash-comma
> line continuation (as in earlier revisions of this doc) reintroduces the bug.

- `_ADMIN_UIDS` — the uid from step 5b (`$ADMIN_UID`), so your first login becomes
  an admin. You can seed several, comma-separated.
- `_ALLOWED_EMAILS` — **the access whitelist.** Only these may log in and use the
  app (checked after Firebase token verification, on REST *and* WebSocket). Each
  entry is an exact email (`a@x.com`) or a whole domain (`@yourco.com`), comma-
  separated. **Empty = open sign-in** (anyone with a valid Firebase account). This
  is the real access gate — CORS/`_ALLOWED_ORIGINS` only stops other *browsers*,
  not scripts. Start with just your email; add more or a domain later + redeploy.
- `_ALLOWED_ORIGINS` — the Firebase Hosting domains (adjust to your real hosting
  site id). Before the frontend exists you can smoke-test with `"*"`.

> ⚠️ `cloudbuild.yaml` deploys with `--set-env-vars`, which **replaces** the full
> env set each run. All desired vars are in that file / these substitutions — a
> redeploy never silently drops one. Secrets are separate (`--set-secrets`) and
> are not touched by env changes.

### First deploy without a build config (alternative / manual)
If you prefer a manual first deploy after building the image yourself:
```bash
gcloud auth configure-docker ${REGION}-docker.pkg.dev
IMAGE="${REGION}-docker.pkg.dev/${PROJECT_ID}/${AR_REPO}/${SERVICE}:manual"
docker build -f clients/tastyhub/Dockerfile -t "$IMAGE" .
docker push "$IMAGE"
gcloud run deploy "$SERVICE" --image="$IMAGE" --region="$REGION" \
  --allow-unauthenticated --port=8080 --cpu=1 --memory=1Gi \
  --min-instances=0 --max-instances=1 --timeout=3600 --concurrency=40 \
  --labels=client_id=tastyhub,app=cookbot \
  --set-secrets=OPENAI_API_KEY=openai-key:latest,API_KEY=api-key:latest \
  --set-env-vars="^;^GOOGLE_CLOUD_PROJECT=$PROJECT_ID;FIRESTORE_DATABASE=(default);TENANT_ID=tastyhub;ALLOWED_ORIGINS=*;ALLOWED_EMAILS=pawe213@gmail.com;ADMIN_UIDS=$ADMIN_UID;DEFAULT_DAILY_TOKEN_LIMIT=1000000;DEFAULT_MONTHLY_TOKEN_LIMIT=10000000;QUOTA_TIMEZONE=Europe/Warsaw;LOG_LEVEL=INFO"
```
> The `^;^` prefix makes `;` the KEY=VALUE delimiter, so commas inside list values
> (ALLOWED_ORIGINS, ALLOWED_EMAILS, ADMIN_UIDS) stay intact. Do **not** use `@` as
> the delimiter — emails contain `@`. Multiple whitelisted emails go comma-separated
> inside the value: `ALLOWED_EMAILS=a@x.com,b@y.com`.

---

## 7. Smoke test the deployed backend

```bash
URL=$(gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)')
echo "Service URL: $URL"

# Health (no auth)
curl -s "$URL/health"
# → {"status":"ok","tenant":"tastyhub","version":"0.1.0"}

# Create a session (needs the API key you stored in Secret Manager)
API_KEY=$(gcloud secrets versions access latest --secret=api-key)
curl -s -X POST "$URL/v1/sessions" -H "x-api-key: $API_KEY"
# → {"session_id":"..."}
```

If `/health` is green but `/v1/sessions` 500s, check logs:
```bash
gcloud run services logs read "$SERVICE" --region="$REGION" --limit=50
```
The usual first-deploy cause is the runtime SA missing `roles/datastore.user`
(step 5) or the Firestore db not existing (step 2).

---

## 8. Save the service URL

You'll need it in **Part 2** for the frontend:
- REST is proxied same-origin via a Firebase Hosting rewrite → Cloud Run.
- The **WebSocket** points at this Cloud Run URL directly (Hosting WS-over-rewrite
  is unreliable), i.e. the frontend's `VITE_WS_BASE = wss://<cloud-run-host>`.

```bash
gcloud run services describe "$SERVICE" --region="$REGION" --format='value(status.url)'
```

---

# PART 2 — Frontend → Firebase Hosting + real Firebase Auth

The frontend is a static Vite/React SPA served by **Firebase Hosting** (free CDN).
Hosting rewrites `/v1/**` and `/health` to the Cloud Run backend, so REST is
**same-origin** (no CORS). The **WebSocket** connects directly to the Cloud Run
URL (`VITE_WS_BASE`), carrying the Firebase ID token as a `token` query param.

## 9. Firebase project + Auth (one-time)

You need a Firebase project *on the same GCP project* as the backend (Firebase and
GCP projects are 1:1). If you haven't already:

1. **Firebase console** → add project → select your existing GCP `$PROJECT_ID`.
2. **Authentication → Sign-in method → Email/Password → Enable** (same as step 5b).
3. **Project settings → General → Your apps → Add app → Web (</>)** → register.
   Copy the shown `firebaseConfig` values — you'll paste them into the frontend env.

Set the default Firebase project for the CLI (edit `.firebaserc` or run):
```bash
firebase login          # if not already
firebase use --add      # pick $PROJECT_ID, or edit .firebaserc's "default"
```

## 10. Frontend production env (one-time; edit to change config)

```bash
cd frontend
cp .env.production.example .env.production
```
Edit `frontend/.env.production` and fill in:
- `VITE_WS_BASE` — the `wss://` form of the Cloud Run URL from step 8, e.g.
  `wss://cookbot-tastyhub-abc123-ew.a.run.app`.
- `VITE_FIREBASE_*` — the web app config values from step 9.
- `VITE_DEV_MODE=false` (so the app uses real Firebase auth, not the dev bypass).
- `VITE_API_BASE=` stays **empty** (REST is same-origin via the Hosting rewrite).

`.env.production` is gitignored — never commit it. (These Firebase values are not
secrets — they ship in the client bundle — but keep the file untracked anyway.)

## 11. Build + deploy the frontend

```bash
cd frontend
npm ci                  # first time, or after dependency changes
npm run build           # → frontend/dist  (tsc -b + vite build)
cd ..

firebase deploy --only hosting
```

The deploy prints your Hosting URL(s), typically:
- `https://<project-id>.web.app`
- `https://<project-id>.firebaseapp.com`

> These must match the backend's `ALLOWED_ORIGINS` (step 6). If your real Hosting
> domain differs from `feedek.web.app`, redeploy the backend with the correct
> `_ALLOWED_ORIGINS` so the WebSocket origin check passes.

## 12. Smoke test the full stack

1. Open the Hosting URL. You should see the **Feedek** login screen (not the dev
   bypass — real auth, because `VITE_DEV_MODE=false`).
2. Log in with the admin account from step 5b (email + the temporary password).
3. Confirm: chat streams a reply (WebSocket connected), the **Admin** tab is
   visible (your uid is in `ADMIN_UIDS`), and Źródła/Kalendarz load.
4. Try a **non-whitelisted** email (if you have one) → login is refused by the
   backend (403 / the app shows an error) because of `ALLOWED_EMAILS`.

If chat REST works but the **WebSocket won't connect**: check `VITE_WS_BASE` points
at the Cloud Run host with `wss://`, and that the backend `ALLOWED_ORIGINS`
includes your Hosting origin. WS auth failures are logged as `ws_token_verify_failed`
or `ws_email_not_allowed` in Cloud Run logs.

## 13. Redeploys

- **Frontend only:** `cd frontend && npm run build && cd .. && firebase deploy --only hosting`
- **Backend only:** re-run section 6.
- They are independent — a frontend change never requires a backend redeploy and
  vice versa.

---

## Rollback

Cloud Run keeps every revision. To roll back to the previous good one:
```bash
gcloud run revisions list --service="$SERVICE" --region="$REGION"
gcloud run services update-traffic "$SERVICE" --region="$REGION" \
  --to-revisions=<PREVIOUS_REVISION>=100
```

---

## Cost posture

- Cloud Run `--min-instances=0` → **scales to zero, $0 when idle**; you pay only
  per request-second while serving. Free tier covers light traffic.
- `--memory=1Gi` is sized for the lazy in-RAM Frisco product index (~50 MB feed →
  ~14k objects + inverted index, built on first `/v1/grocery` call). The GCS blob
  cache that removes the per-instance re-download is a Phase 4 deferred item.
- No Load Balancer, no idle VM.
