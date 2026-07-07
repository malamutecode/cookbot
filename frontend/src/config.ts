// Runtime config, driven by Vite build-time env vars (VITE_*). Local dev keeps
// working with zero env set; production sets these in .env.production / the build.

const env = import.meta.env

// API + WebSocket base URLs.
// - Local dev: empty API_BASE (same-origin via the Vite proxy); WS_BASE derived
//   from the current host.
// - Production (Firebase Hosting): API_BASE stays "" because /v1 + /health are
//   rewritten same-origin to Cloud Run; WS_BASE must point at the Cloud Run URL
//   directly (browsers can't set headers on a WebSocket, and Hosting WS-over-
//   rewrite is unreliable), e.g. wss://cookbot-tastyhub-xxxx.run.app
export const API_BASE = env.VITE_API_BASE ?? ''
export const WS_BASE =
  env.VITE_WS_BASE ??
  `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`

// DEV_MODE: when true, the app uses the local dev-uid bypass instead of real
// Firebase Auth. Defaults to true ONLY when no Firebase config is provided, so a
// production build (which sets VITE_FIREBASE_*) is secure by default. Force it
// with VITE_DEV_MODE=true|false.
const hasFirebaseConfig = Boolean(env.VITE_FIREBASE_API_KEY && env.VITE_FIREBASE_PROJECT_ID)
export const DEV_MODE =
  env.VITE_DEV_MODE !== undefined ? env.VITE_DEV_MODE === 'true' : !hasFirebaseConfig

// Dev-only identity bypass (only used when DEV_MODE is true).
export const DEV_API_KEY = env.VITE_API_KEY ?? 'tk_dev_local'
export const DEV_UID = 'dev_user'

// When VITE_TEST_USER=true, skip the login screen and auto-authenticate as the
// dev user. Local smoke-testing only — never set in prod.
export const TEST_USER = env.VITE_TEST_USER === 'true'

// Firebase web app config (from the Firebase console → Project settings → your
// web app). Used to initialize the client SDK for real email/password auth.
export const FIREBASE_CONFIG = {
  apiKey: env.VITE_FIREBASE_API_KEY ?? '',
  authDomain: env.VITE_FIREBASE_AUTH_DOMAIN ?? '',
  projectId: env.VITE_FIREBASE_PROJECT_ID ?? '',
  appId: env.VITE_FIREBASE_APP_ID ?? '',
}
