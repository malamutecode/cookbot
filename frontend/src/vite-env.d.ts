/// <reference types="vite/client" />

// Typed access to the app's build-time env vars (import.meta.env.VITE_*).
// All optional strings — config.ts applies local-dev defaults when unset.
interface ImportMetaEnv {
  readonly VITE_API_BASE?: string
  readonly VITE_WS_BASE?: string
  readonly VITE_DEV_MODE?: string
  readonly VITE_TEST_USER?: string
  readonly VITE_API_KEY?: string
  readonly VITE_FIREBASE_API_KEY?: string
  readonly VITE_FIREBASE_AUTH_DOMAIN?: string
  readonly VITE_FIREBASE_PROJECT_ID?: string
  readonly VITE_FIREBASE_APP_ID?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
