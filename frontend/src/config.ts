export const API_BASE = ''          // empty = same origin via Vite proxy
export const WS_BASE  = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
export const DEV_MODE = true
export const DEV_API_KEY = 'tk_dev_local'
export const DEV_UID     = 'dev_user'

// When VITE_TEST_USER=true, skip the login screen and auto-authenticate as the
// dev user (admin@admin.com). Local smoke-testing only — never set in prod.
export const TEST_USER = import.meta.env.VITE_TEST_USER === 'true'
