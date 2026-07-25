#!/usr/bin/env bash
#
# Deploy the Feedek backend to Cloud Run via Cloud Build.
# Repeatable — run this for every backend release.
#
#   ./infra/deploy-backend.sh [--dry-run] [--no-smoke]
#
# Config comes from infra/deploy.env (see infra/deploy.env.example).
# One-time project setup lives in ./infra/bootstrap.sh.

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

SMOKE=1
for arg in "$@"; do
  case "$arg" in
    --dry-run)  DRY_RUN=1 ;;
    --no-smoke) SMOKE=0 ;;
    -h|--help)
      usage_and_exit "usage: ./infra/deploy-backend.sh [--dry-run] [--no-smoke]

  --dry-run   print the commands without running them
  --no-smoke  skip the post-deploy /health check

Reads config from infra/deploy.env. Any variable already set in your shell wins,
so one-off overrides work:  ALLOWED_EMAILS=a@b.com ./infra/deploy-backend.sh" ;;
    *) die "unknown argument: $arg (try --help)" ;;
  esac
done

load_config
require_gcloud
check_service_matches_hosting

: "${ALLOWED_ORIGINS:=*}"
: "${ALLOWED_EMAILS:=}"
: "${ADMIN_UIDS:=}"
: "${DEFAULT_DAILY_TOKEN_LIMIT:=0}"
: "${DEFAULT_MONTHLY_TOKEN_LIMIT:=0}"

step "Backend deploy — $SERVICE"
info "project          $PROJECT_ID"
info "region           $REGION"
info "image repo       $REGION-docker.pkg.dev/$PROJECT_ID/$AR_REPO/$SERVICE"
info "allowed origins  $ALLOWED_ORIGINS"
info "allowed emails   ${ALLOWED_EMAILS:-<empty — OPEN sign-in>}"
info "admin uids       ${ADMIN_UIDS:-<none>}"
info "token limits     daily=$DEFAULT_DAILY_TOKEN_LIMIT monthly=$DEFAULT_MONTHLY_TOKEN_LIMIT"

[[ -z "$ALLOWED_EMAILS" ]] && warn "ALLOWED_EMAILS is empty — ANY Firebase account can sign in."
[[ "$ALLOWED_ORIGINS" == "*" ]] && warn "ALLOWED_ORIGINS is '*' — fine for a smoke test, not for prod."

# `--substitutions` splits on ',' unconditionally, which would shred the
# comma-separated list values. The `^;^` prefix switches the delimiter to ';'.
SUBS="^;^"
SUBS+="_REGION=$REGION"
SUBS+=";_AR_REPO=$AR_REPO"
SUBS+=";_SERVICE=$SERVICE"
SUBS+=";_ALLOWED_ORIGINS=$ALLOWED_ORIGINS"
SUBS+=";_ALLOWED_EMAILS=$ALLOWED_EMAILS"
SUBS+=";_ADMIN_UIDS=$ADMIN_UIDS"
SUBS+=";_DEFAULT_DAILY_TOKEN_LIMIT=$DEFAULT_DAILY_TOKEN_LIMIT"
SUBS+=";_DEFAULT_MONTHLY_TOKEN_LIMIT=$DEFAULT_MONTHLY_TOKEN_LIMIT"

# Build context MUST be the repo root — the client's uv.lock pins editable path
# deps at ../../packages/..., and the Dockerfile COPYs from the root.
step "Submitting to Cloud Build (build → push → deploy; ~3-6 min)"
cd "$REPO_ROOT"
run gcloud builds submit \
  --project="$PROJECT_ID" \
  --config clients/tastyhub/cloudbuild.yaml \
  --substitutions="$SUBS" \
  .

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  step "Dry run complete — nothing was deployed."
  exit 0
fi

URL="$(service_url)"
[[ -n "$URL" ]] || die "deploy reported success but the service URL could not be read"

step "Deployed"
ok "$URL"

if [[ "$SMOKE" == "1" ]]; then
  step "Smoke test — GET /health"
  if command -v curl >/dev/null 2>&1; then
    if curl -fsS --max-time 60 "$URL/health"; then
      printf '\n'
      ok "health check passed"
    else
      printf '\n'
      warn "health check failed. Recent logs:"
      gcloud run services logs read "$SERVICE" --region="$REGION" \
        --project="$PROJECT_ID" --limit=30 || true
      die "backend is deployed but unhealthy"
    fi
  else
    warn "curl not found — skipping smoke test. Check $URL/health manually."
  fi
fi

# The frontend's VITE_WS_BASE must point at this host. A new service (or a
# recreated one) changes the hostname, which silently breaks the WebSocket.
WS_BASE="wss://${URL#https://}"
CURRENT_WS="$(grep -E '^VITE_WS_BASE=' "$REPO_ROOT/frontend/.env.production" 2>/dev/null | cut -d= -f2- || true)"
if [[ -n "$CURRENT_WS" && "$CURRENT_WS" != "$WS_BASE" ]]; then
  step "Frontend WebSocket target changed"
  warn "frontend/.env.production has VITE_WS_BASE=$CURRENT_WS"
  warn "the deployed service is    $WS_BASE"
  info "Update that file and redeploy the frontend:  ./infra/deploy-frontend.sh"
fi

step "Done"
dim "logs:     gcloud run services logs read $SERVICE --region=$REGION --limit=50"
dim "rollback: gcloud run revisions list --service=$SERVICE --region=$REGION"
