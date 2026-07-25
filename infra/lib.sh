# shellcheck shell=bash
# Shared helpers for the infra/ scripts. Sourced, never executed directly.

# Repo root = parent of infra/. Resolved from this file's own location, so the
# scripts work from any cwd (git bash, Linux, macOS, CI).
INFRA_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$INFRA_DIR/.." && pwd)"
export INFRA_DIR REPO_ROOT

# ── output ───────────────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  _C_RESET=$'\033[0m'; _C_BLUE=$'\033[1;34m'; _C_GREEN=$'\033[1;32m'
  _C_YELLOW=$'\033[1;33m'; _C_RED=$'\033[1;31m'; _C_DIM=$'\033[2m'
else
  _C_RESET=''; _C_BLUE=''; _C_GREEN=''; _C_YELLOW=''; _C_RED=''; _C_DIM=''
fi

step() { printf '\n%s==>%s %s\n' "$_C_BLUE" "$_C_RESET" "$*"; }
info() { printf '    %s\n' "$*"; }
dim()  { printf '    %s%s%s\n' "$_C_DIM" "$*" "$_C_RESET"; }
ok()   { printf '%s  ok%s %s\n' "$_C_GREEN" "$_C_RESET" "$*"; }
warn() { printf '%swarn%s %s\n' "$_C_YELLOW" "$_C_RESET" "$*" >&2; }
die()  { printf '%sfail%s %s\n' "$_C_RED" "$_C_RESET" "$*" >&2; exit 1; }

# Print a command, then run it (unless DRY_RUN=1).
run() {
  printf '    %s$ %s%s\n' "$_C_DIM" "$*" "$_C_RESET"
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    return 0
  fi
  "$@"
}

# Like run(), but a non-zero exit is expected/harmless (e.g. "already exists").
run_ok_if_exists() {
  printf '    %s$ %s%s\n' "$_C_DIM" "$*" "$_C_RESET"
  if [[ "${DRY_RUN:-0}" == "1" ]]; then
    return 0
  fi
  if ! "$@"; then
    warn "command failed — continuing (already exists / not fatal for bootstrap)"
  fi
}

# ── config ───────────────────────────────────────────────────────────────────
# Load infra/deploy.env WITHOUT clobbering vars already set in the environment,
# so `PROJECT_ID=other ./infra/deploy-backend.sh` works as an override.
load_config() {
  local env_file="$INFRA_DIR/deploy.env"
  if [[ ! -f "$env_file" ]]; then
    die "missing $env_file
    Create it first:  cp infra/deploy.env.example infra/deploy.env
    then edit the values (PROJECT_ID, ADMIN_UIDS, ALLOWED_EMAILS, ...)."
  fi

  local line key val
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"                       # tolerate CRLF (Windows editors)
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "$line" =~ ^[[:space:]]*# ]] && continue
    [[ "$line" != *=* ]] && continue
    key="${line%%=*}"; val="${line#*=}"
    key="${key//[[:space:]]/}"
    # Strip one layer of surrounding quotes, if present.
    [[ "$val" == \"*\" ]] && val="${val:1:${#val}-2}"
    [[ "$val" == \'*\' ]] && val="${val:1:${#val}-2}"
    # Only set if not already defined in the environment.
    if [[ -z "${!key+x}" ]]; then
      export "$key=$val"
    fi
  done < "$env_file"

  : "${PROJECT_ID:?PROJECT_ID not set (infra/deploy.env)}"
  : "${REGION:?REGION not set (infra/deploy.env)}"
  : "${SERVICE:?SERVICE not set (infra/deploy.env)}"
  : "${AR_REPO:?AR_REPO not set (infra/deploy.env)}"
}

# ── preflight ────────────────────────────────────────────────────────────────
need_cmd() {
  command -v "$1" >/dev/null 2>&1 || die "'$1' not found on PATH. $2"
}

require_gcloud() {
  need_cmd gcloud "Install the Google Cloud SDK: https://cloud.google.com/sdk/docs/install"
  local account
  account="$(gcloud config get-value account 2>/dev/null || true)"
  if [[ -z "$account" || "$account" == "(unset)" ]]; then
    die "gcloud is not authenticated. Run:  gcloud auth login"
  fi
  dim "gcloud account: $account"
}

require_firebase() {
  need_cmd firebase "Install it with:  npm install -g firebase-tools"
}

# Warn (don't fail) when firebase.json's Cloud Run rewrite target disagrees with
# $SERVICE — that mismatch silently breaks the same-origin /v1 proxy.
check_service_matches_hosting() {
  local fj="$REPO_ROOT/firebase.json"
  [[ -f "$fj" ]] || return 0
  if ! grep -q "\"serviceId\": *\"$SERVICE\"" "$fj"; then
    warn "firebase.json has no rewrite with serviceId \"$SERVICE\"."
    warn "The Hosting /v1 proxy will not reach this service. Check both files."
  fi
}

# Resolve the deployed Cloud Run URL (empty if the service doesn't exist yet).
service_url() {
  gcloud run services describe "$SERVICE" \
    --region="$REGION" --project="$PROJECT_ID" \
    --format='value(status.url)' 2>/dev/null || true
}

# Standard --help/--dry-run preamble handling shared by the deploy scripts.
usage_and_exit() { printf '%s\n' "$1"; exit 0; }
