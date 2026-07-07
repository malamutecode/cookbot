# Feedek — Deployment Runbook

Backend → **Cloud Run**, frontend → **Firebase Hosting** (frontend part added in
the next phase). This file is the ordered list of commands **you** run. Claude
prepared the Dockerfile / cloudbuild.yaml / config but does not execute deploys.

> **Part 1 (this document) covers the BACKEND only.** Frontend + real Firebase
> Auth is a separate follow-up.

---

## 0. One-time setup — variables

Set these in your shell first. Replace the project id with your **production**
GCP project (the repo's `.env` uses `cookbot-local-dev`, which is a local-dev
project — decide whether prod reuses it or is a new project).

```bash
export PROJECT_ID=<YOUR_PROJECT_ID>          # e.g. feedek-prod
export REGION=europe-west1
export AR_REPO=feedek                         # Artifact Registry repo name
export SERVICE=cookbot-tastyhub               # Cloud Run service name

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
# OpenAI key
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

## 6. Deploy (repeat for every release)

Run from the **repo root** (the build context must be the root — the client's
`uv.lock` pins editable path deps at `../../packages/...`).

```bash
gcloud builds submit --config clients/tastyhub/cloudbuild.yaml \
  --substitutions=_REGION=$REGION,_AR_REPO=$AR_REPO,_SERVICE=$SERVICE,\
_ALLOWED_ORIGINS="https://feedek.web.app,https://feedek.firebaseapp.com",\
_ADMIN_UIDS="<YOUR_FIREBASE_UID>",\
_DEFAULT_DAILY_TOKEN_LIMIT="1000000",\
_DEFAULT_MONTHLY_TOKEN_LIMIT="10000000"
```

- `_ADMIN_UIDS` — your Firebase uid, so your first login becomes an admin (find
  it in the Firebase console → Authentication, or from a decoded ID token).
  Until Part 2 wires real auth you can leave it empty and set it on the redeploy.
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
  --min-instances=0 --max-instances=4 --timeout=3600 --concurrency=40 \
  --labels=client_id=tastyhub,app=cookbot \
  --set-secrets=OPENAI_API_KEY=openai-key:latest,API_KEY=api-key:latest \
  --set-env-vars=GOOGLE_CLOUD_PROJECT=$PROJECT_ID,FIRESTORE_DATABASE='(default)',TENANT_ID=tastyhub,ALLOWED_ORIGINS='*',ADMIN_UIDS='',DEFAULT_DAILY_TOKEN_LIMIT=1000000,DEFAULT_MONTHLY_TOKEN_LIMIT=10000000,QUOTA_TIMEZONE=Europe/Warsaw,LOG_LEVEL=INFO
```

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
