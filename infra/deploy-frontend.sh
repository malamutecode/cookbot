#!/usr/bin/env bash
#
# Build the Vite SPA and deploy it to Firebase Hosting.
# Repeatable — run this for every frontend release.
#
#   ./infra/deploy-frontend.sh [--dry-run] [--ci] [--skip-build]
#
# Independent of the backend: a frontend change never needs a backend redeploy.

set -euo pipefail
source "$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/lib.sh"

USE_CI=0
SKIP_BUILD=0
for arg in "$@"; do
  case "$arg" in
    --dry-run)    DRY_RUN=1 ;;
    --ci)         USE_CI=1 ;;
    --skip-build) SKIP_BUILD=1 ;;
    -h|--help)
      usage_and_exit "usage: ./infra/deploy-frontend.sh [--dry-run] [--ci] [--skip-build]

  --dry-run     print the commands without running them
  --ci          use 'npm ci' instead of 'npm install' (clean, lockfile-exact)
  --skip-build  deploy the existing frontend/dist as-is" ;;
    *) die "unknown argument: $arg (try --help)" ;;
  esac
done

load_config
require_firebase
need_cmd npm "Install Node.js: https://nodejs.org"

FRONTEND_DIR="$REPO_ROOT/frontend"
ENV_PROD="$FRONTEND_DIR/.env.production"

step "Frontend deploy — Firebase Hosting ($PROJECT_ID)"

# Vite bakes VITE_* values into the bundle at BUILD time, so a missing or
# placeholder .env.production ships a broken app rather than failing loudly.
if [[ ! -f "$ENV_PROD" ]]; then
  die "missing frontend/.env.production
    cp frontend/.env.production.example frontend/.env.production
    then fill in VITE_WS_BASE + VITE_FIREBASE_* (see DEPLOY.md section 10)."
fi

if grep -qE '<your-|<YOUR_' "$ENV_PROD"; then
  die "frontend/.env.production still contains placeholder values (<your-...>).
    Fill them in before deploying — Vite bakes them into the bundle."
fi

WS_BASE="$(grep -E '^VITE_WS_BASE=' "$ENV_PROD" | cut -d= -f2- || true)"
[[ -n "$WS_BASE" ]] || die "VITE_WS_BASE is empty in frontend/.env.production"
info "ws base   $WS_BASE"

# Cross-check against the live backend so a stale WS host is caught before deploy.
if command -v gcloud >/dev/null 2>&1; then
  LIVE_URL="$(service_url)"
  if [[ -n "$LIVE_URL" ]]; then
    EXPECTED_WS="wss://${LIVE_URL#https://}"
    if [[ "$WS_BASE" != "$EXPECTED_WS" ]]; then
      warn "VITE_WS_BASE does not match the deployed Cloud Run service:"
      warn "  .env.production : $WS_BASE"
      warn "  live service    : $EXPECTED_WS"
      warn "The WebSocket will fail to connect. Fix .env.production first."
      if [[ "${DRY_RUN:-0}" != "1" ]]; then
        read -r -p "    Continue anyway? [y/N] " reply
        [[ "$reply" == [yY] ]] || die "aborted"
      fi
    else
      ok "ws base matches the live backend"
    fi
  fi
fi

cd "$FRONTEND_DIR"

if [[ "$SKIP_BUILD" == "1" ]]; then
  [[ -d dist ]] || die "--skip-build given but frontend/dist does not exist"
  warn "skipping build — deploying the existing frontend/dist"
else
  if [[ "$USE_CI" == "1" || ! -d node_modules ]]; then
    step "Installing dependencies"
    if [[ "$USE_CI" == "1" ]]; then
      run npm ci
    else
      run npm install
    fi
  fi

  step "Building (tsc -b + vite build)"
  run npm run build
  [[ "${DRY_RUN:-0}" == "1" ]] || [[ -d dist ]] || die "build produced no frontend/dist"
fi

step "Deploying to Firebase Hosting"
cd "$REPO_ROOT"
run firebase deploy --only hosting --project "$PROJECT_ID"

if [[ "${DRY_RUN:-0}" == "1" ]]; then
  step "Dry run complete — nothing was deployed."
  exit 0
fi

SITE="${FIREBASE_SITE:-$PROJECT_ID}"
step "Done"
ok "https://${SITE}.web.app"
dim "also:  https://${SITE}.firebaseapp.com"
dim "Both must appear in the backend's ALLOWED_ORIGINS (infra/deploy.env)."
