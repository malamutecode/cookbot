export const API_BASE = ''          // empty = same origin via Vite proxy
export const WS_BASE  = `${window.location.protocol === 'https:' ? 'wss' : 'ws'}://${window.location.host}`
export const DEV_MODE = true
export const DEV_API_KEY = 'tk_dev_local'
export const DEV_UID     = 'dev_user'
