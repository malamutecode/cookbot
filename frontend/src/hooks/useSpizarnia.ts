import { useState, useCallback } from 'react'
import { SpizarniaItem } from '../types'
import { API_BASE, DEV_MODE, DEV_API_KEY, DEV_UID } from '../config'

function authHeaders(idToken: string | null, extra?: Record<string, string>): Record<string, string> {
  const h: Record<string, string> = { ...extra }
  if (idToken) {
    h['Authorization'] = `Bearer ${idToken}`
  } else {
    h['x-api-key'] = DEV_API_KEY
    if (DEV_MODE) h['x-dev-uid'] = DEV_UID
  }
  return h
}

export function useSpizarnia(idToken: string | null) {
  const [items, setItems] = useState<SpizarniaItem[]>([])

  const load = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/v1/spizarnia`, {
        headers: authHeaders(idToken),
      })
      if (!resp.ok) return
      const data = await resp.json()
      setItems(data.items ?? [])
    } catch {
      setItems([])
    }
  }, [idToken])

  const add = useCallback(async (name: string, quantity = '') => {
    if (!name.trim()) return
    await fetch(`${API_BASE}/v1/spizarnia/items`, {
      method: 'POST',
      headers: authHeaders(idToken, { 'Content-Type': 'application/json' }),
      body: JSON.stringify({ items: [{ name: name.trim(), quantity }] }),
    })
    await load()
  }, [idToken, load])

  const remove = useCallback(async (name: string) => {
    await fetch(`${API_BASE}/v1/spizarnia/items/${encodeURIComponent(name)}`, {
      method: 'DELETE',
      headers: authHeaders(idToken),
    })
    await load()
  }, [idToken, load])

  return { items, load, add, remove }
}

export { authHeaders }
