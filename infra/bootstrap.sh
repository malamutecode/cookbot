#!/usr/bin/env bash
#
# ONE-TIME project setup for Feedek on a fresh GCP project.
# Idempotent: safe to re-run — steps that already exist are skipped or warn.
#
#   ./infra/bootstrap.sh [--dry-run] [--skip-secrets]
#
# Covers DEPLOY.md sections 1-5: APIs, Firestore, Artifact Registry, Secret
# Manager, and IAM for the runtime + build service accounts.
# It does NOT deploy anything — run ./infra/deploy-backend.sh afterwards.
#
# Secret VALUES are read interactively and never written to disk or the log.

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

SKIP_SECRETS=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)      DRY_RUN=1 ;;
    --skip-secrets) SKIP_SECRETS=1 ;;
    -h|--help)
      usage_and_exit "usage: ./infra/bootstrap.sh [--dry-run] [--skip-secrets]

  --dry-run        print the commands without running them
  --skip-secrets   don't touch Secret Manager (secrets already exist)

One-time setup for a fresh GCP project. Idempotent. Does not deploy." ;;
    *) die "unknown argument: $arg (try --help)" ;;
  esac
done

load_config
require_gcloud

step "Bootstrap — project $PROJECT_ID (region $REGION)"
warn "This creates billable GCP resources in project '$PROJECT_ID'."
if [[ "${DRY_RUN:-0}" != "1" ]]; then
  read -r -p "    Continue? [y/N] " reply
  [[ "$reply" == [yY] ]] || die "aborted"
fi

GCP="--project=$PROJECT_ID"

# ── 1. APIs ──────────────────────────────────────────────────────────────────
step "1/6 Enabling APIs (no-op if already enabled)"
run gcloud services enable $GCP \
  run.googleapis.com \
  cloudbuild.googleapis.com \
  artifactregistry.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  firebase.googleapis.com \
  identitytoolkit.googleapis.com

# ── 2. Firestore ─────────────────────────────────────────────────────────────
step "2/6 Firestore database (native mode, '(default)')"
if [[ "${DRY_RUN:-0}" != "1" ]] && \
   gcloud firestore databases describe --database='(default)' $GCP >/dev/null 2>&1; then
  ok "already exists — skipping"
else
  run_ok_if_exists gcloud firestore databases create $GCP \
    --location="$REGION" --type=firestore-native
fi

# ── 3. Artifact Registry ─────────────────────────────────────────────────────
step "3/6 Artifact Registry repo '$AR_REPO'"
if [[ "${DRY_RUN:-0}" != "1" ]] && \
   gcloud artifacts repositories describe "$AR_REPO" \
     --location="$REGION" $GCP >/dev/null 2>&1; then
  ok "already exists — skipping"
else
  run_ok_if_exists gcloud artifacts repositories create "$AR_REPO" $GCP \
    --repository-format=docker \
    --location="$REGION" \
    --description="Feedek container images"
fi

# ── 4. Secrets ───────────────────────────────────────────────────────────────
# Values are piped from a prompt (read -s) — never argv, never a file, so they
# stay out of shell history and out of this script's printed commands.
create_secret_interactive() {
  local name="$1" prompt="$2" generated="${3:-}"

  if gcloud secrets describe "$name" $GCP >/dev/null 2>&1; then
    ok "secret '$name' already exists — leaving it alone"
    dim "rotate with: printf '%s' 'NEW' | gcloud secrets versions add $name --data-file=-"
    return 0
  fi

  local value=""
  if [[ -n "$generated" ]]; then
    value="$generated"
    info "generating a random value for '$name'"
  else
    printf '    %s' "$prompt"
    read -rs value
    printf '\n'
    [[ -n "$value" ]] || die "no value entered for '$name'"
  fi

  printf '    %s$ gcloud secrets create %s --data-file=- (value read from prompt)%s\n' \
    "$_C_DIM" "$name" "$_C_RESET"
  printf '%s' "$value" | gcloud secrets create "$name" $GCP \
    --data-file=- --replication-policy=automatic
  ok "created secret '$name'"
}

step "4/6 Secret Manager (openai-key, api-key)"
if [[ "$SKIP_SECRETS" == "1" ]]; then
  warn "--skip-secrets given — skipping"
elif [[ "${DRY_RUN:-0}" == "1" ]]; then
  dim "would create secrets 'openai-key' and 'api-key' (values read interactively)"
else
  create_secret_interactive openai-key "Paste your OpenAI API key (sk-...): "

  # The widget/API key is ours to invent, so generate it rather than asking.
  if command -v openssl >/dev/null 2>&1; then
    GENERATED_API_KEY="tk_live_$(openssl rand -hex 24)"
  else
    GENERATED_API_KEY="tk_live_$(head -c 24 /dev/urandom | od -An -tx1 | tr -d ' \n')"
  fi
  create_secret_interactive api-key "" "$GENERATED_API_KEY"
fi

# ── 5. IAM ───────────────────────────────────────────────────────────────────
step "5/6 IAM for the runtime + build service accounts"
if [[ "${DRY_RUN:-0}" == "1" ]]; then
  PROJECT_NUMBER="<project-number>"
else
  PROJECT_NUMBER="$(gcloud projects describe "$PROJECT_ID" --format='value(projectNumber)')"
  [[ -n "$PROJECT_NUMBER" ]] || die "could not resolve the project number for $PROJECT_ID"
fi
RUNTIME_SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
info "service account: $RUNTIME_SA"
dim "Cloud Build and Cloud Run both default to the Compute Engine SA."

for secret in openai-key api-key; do
  run_ok_if_exists gcloud secrets add-iam-policy-binding "$secret" $GCP \
    --member="serviceAccount:${RUNTIME_SA}" \
    --role="roles/secretmanager.secretAccessor" \
    --condition=None
done

# Firestore read/write for the app.
run_ok_if_exists gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/datastore.user" --condition=None

# Cloud Build needs to deploy Cloud Run and act as the runtime SA.
run_ok_if_exists gcloud projects add-iam-policy-binding "$PROJECT_ID" \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/run.admin" --condition=None

run_ok_if_exists gcloud iam service-accounts add-iam-policy-binding "$RUNTIME_SA" $GCP \
  --member="serviceAccount:${RUNTIME_SA}" \
  --role="roles/iam.serviceAccountUser"

# ── 6. Manual follow-ups ─────────────────────────────────────────────────────
step "6/6 Remaining manual steps (cannot be scripted)"
cat <<EOF

    1. Firebase console -> Authentication -> Sign-in method -> Email/Password
       -> ENABLE.  Without this, creating accounts fails.

    2. Firebase console -> Project settings -> General -> Your apps -> Web (</>)
       -> register, then copy the config values into frontend/.env.production
       (see frontend/.env.production.example).

    3. Seed your admin account and capture its uid:

         gcloud auth application-default login
         export GOOGLE_CLOUD_PROJECT=$PROJECT_ID
         cd clients/tastyhub && uv run python scripts/seed_admin.py && cd ../..

       Put the printed uid into ADMIN_UIDS in infra/deploy.env, and SAVE the
       printed temporary password — it is not stored anywhere else.

    4. Deploy:
         ./infra/deploy-backend.sh
         # then set VITE_WS_BASE from the printed URL, and:
         ./infra/deploy-frontend.sh

EOF
ok "bootstrap complete"
