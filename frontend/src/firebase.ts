// Firebase client SDK init for real email/password auth.
// Only initialized when a Firebase config is present (production); in DEV_MODE
// the app uses the local dev-uid bypass and never touches this.

import { initializeApp, type FirebaseApp } from 'firebase/app'
import { getAuth, type Auth } from 'firebase/auth'
import { FIREBASE_CONFIG } from './config'

let _app: FirebaseApp | null = null
let _auth: Auth | null = null

export function firebaseAuth(): Auth {
  if (_auth) return _auth
  if (!FIREBASE_CONFIG.apiKey || !FIREBASE_CONFIG.projectId) {
    throw new Error(
      'Firebase is not configured. Set VITE_FIREBASE_* env vars (see .env.production.example).',
    )
  }
  _app = initializeApp(FIREBASE_CONFIG)
  _auth = getAuth(_app)
  return _auth
}
