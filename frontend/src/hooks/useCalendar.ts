import { useState, useCallback, useRef } from 'react'
import { CalendarDay, CalendarEntry, DEFAULT_MEAL_SLOT } from '../types'
import { daysFromServer, daysToServer } from '../lib/calendar'
import { API_BASE } from '../config'
import { authHeaders } from './useSpizarnia'

/**
 * The meal calendar, stored server-side (STEP 52).
 *
 * There is no localStorage tier: Firestore is the only source, so a plan follows
 * the user across devices and the server has an auditable record of what the
 * agent added. A failed load leaves the calendar empty rather than showing a
 * stale local copy that no longer matches the server.
 *
 * Writes are optimistic — local state updates first, the PUT follows — because
 * drag/drop must not wait on a round-trip to repaint.
 */
export function useCalendar(idToken: string | null) {
  const [days, setDays] = useState<CalendarDay[]>([])
  // Always-current mirror of `days`, so the WS-driven callbacks below can persist
  // the state they just computed without depending on a re-render.
  const daysRef = useRef<CalendarDay[]>([])

  const apply = useCallback((next: CalendarDay[]) => {
    daysRef.current = next
    setDays(next)
  }, [])

  const load = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE}/v1/calendar`, { headers: authHeaders(idToken) })
      if (!resp.ok) return
      const data = await resp.json()
      apply(daysFromServer(data.entries ?? []))
    } catch {
      apply([])
    }
  }, [idToken, apply])

  /** Persist the whole plan. Used by every mutation that isn't a plain delete —
   *  drag/drop, slot moves and agent adds all reshape the day buckets. */
  const save = useCallback(async (next: CalendarDay[]) => {
    apply(next)
    try {
      await fetch(`${API_BASE}/v1/calendar`, {
        method: 'PUT',
        headers: authHeaders(idToken, { 'Content-Type': 'application/json' }),
        body: JSON.stringify({ entries: daysToServer(next) }),
      })
    } catch { /* optimistic: the local view stays; a reload re-syncs from the server */ }
  }, [idToken, apply])

  /** Functional-updater variant — safe from the ChatPanel WS closure, which may
   *  hold a stale `days` snapshot. */
  const update = useCallback((fn: (prev: CalendarDay[]) => CalendarDay[]) => {
    void save(fn(daysRef.current))
  }, [save])

  const addEntry = useCallback((entry: CalendarEntry) => {
    const targetDate = entry.date ?? new Date().toISOString().slice(0, 10)
    // The agent may name a meal section; anything without one lands in obiad.
    const slotted: CalendarEntry = { ...entry, mealSlot: entry.mealSlot ?? DEFAULT_MEAL_SLOT }
    update(prev => {
      const existing = prev.find(d => d.date === targetDate)
      if (existing) {
        if (existing.recipes.some(r => r.id === slotted.id)) return prev  // already there
        return prev.map(d => d.date === targetDate ? { ...d, recipes: [...d.recipes, slotted] } : d)
      }
      return [...prev, { date: targetDate, recipes: [slotted], freeText: '' }]
    })
  }, [update])

  /** Delete by id. Targeted DELETE rather than a whole-state PUT so a removal
   *  can't resurrect entries another device added since this tab last loaded. */
  const removeEntry = useCallback((entryId: string) => {
    apply(daysRef.current.map(d => ({ ...d, recipes: d.recipes.filter(r => r.id !== entryId) })))
    fetch(`${API_BASE}/v1/calendar/entries/${encodeURIComponent(entryId)}`, {
      method: 'DELETE',
      headers: authHeaders(idToken),
    }).catch(() => { /* optimistic — see save() */ })
  }, [idToken, apply])

  return { days, load, save, addEntry, removeEntry }
}
