import { CalendarDay, CalendarEntry, DEFAULT_MEAL_SLOT, MealSlot, MEAL_SLOTS } from '../types'

/** Payload carried by a chip drag. Identifies the entry AND where it came from,
 *  so the drop can remove it from its source before inserting it at the target. */
export interface DragPayload {
  entryId: string
  fromDate: string
  fromSlot: MealSlot
}

/** Slot of an entry, applying the legacy fallback in one place. Entries saved
 *  before STEP 48 have no mealSlot and are treated as `obiad`. */
export function slotOf(entry: CalendarEntry): MealSlot {
  return entry.mealSlot ?? DEFAULT_MEAL_SLOT
}

/** The day record for a date, or an empty one. Never returns undefined so
 *  callers can render a blank day without branching. */
export function getDay(days: CalendarDay[], date: string): CalendarDay {
  return days.find(d => d.date === date) ?? { date, recipes: [], freeText: '' }
}

/** Entries of one day that belong to `slot`, in insertion order. */
export function entriesForSlot(day: CalendarDay, slot: MealSlot): CalendarEntry[] {
  return day.recipes.filter(r => slotOf(r) === slot)
}

/** Apply `updater` to the day at `date`, creating the day when it doesn't exist. */
export function upsertDay(
  days: CalendarDay[],
  date: string,
  updater: (d: CalendarDay) => CalendarDay,
): CalendarDay[] {
  if (days.some(d => d.date === date)) {
    return days.map(d => (d.date === date ? updater(d) : d))
  }
  return [...days, updater({ date, recipes: [], freeText: '' })]
}

/**
 * Move an entry to (toDate, toSlot).
 *
 * This is a MOVE, not a copy: the entry is removed from wherever it currently
 * lives before being appended to the target slot. Dropping an entry onto the slot
 * it already occupies is a no-op — without that guard the remove/append pair would
 * reorder the chip to the end of its own slot on every stray drop.
 */
export function moveEntry(
  days: CalendarDay[],
  payload: DragPayload,
  toDate: string,
  toSlot: MealSlot,
): CalendarDay[] {
  if (payload.fromDate === toDate && payload.fromSlot === toSlot) return days

  const source = days.find(d => d.date === payload.fromDate)
  const entry = source?.recipes.find(r => r.id === payload.entryId)
  if (!entry) return days  // stale drag (entry deleted mid-drag) — ignore

  const withoutEntry = days.map(d => ({
    ...d,
    recipes: d.recipes.filter(r => r.id !== payload.entryId),
  }))
  const moved: CalendarEntry = { ...entry, date: toDate, mealSlot: toSlot }
  return upsertDay(withoutEntry, toDate, d => ({ ...d, recipes: [...d.recipes, moved] }))
}

/** Remove an entry by id from a specific day. */
export function removeEntry(days: CalendarDay[], date: string, entryId: string): CalendarDay[] {
  return days.map(d => (d.date === date ? { ...d, recipes: d.recipes.filter(r => r.id !== entryId) } : d))
}

/** Every ingredient from the entries whose ids are in `selectedIds`, in calendar
 *  order (day, then slot order, then insertion order). Duplicates are preserved —
 *  the ShoppingListAgent is what merges and sums them. */
export function ingredientsForSelection(
  days: CalendarDay[],
  dates: string[],
  selectedIds: ReadonlySet<string>,
): string[] {
  const out: string[] = []
  dates.forEach(date => {
    const day = getDay(days, date)
    MEAL_SLOTS.forEach(slot => {
      entriesForSlot(day, slot).forEach(entry => {
        if (selectedIds.has(entry.id)) out.push(...entry.ingredients)
      })
    })
  })
  return out
}

/** Ingredients of every entry on the given dates — the "whole days" export. */
export function ingredientsForDates(days: CalendarDay[], dates: string[]): string[] {
  return dates.flatMap(date => getDay(days, date).recipes.flatMap(r => r.ingredients))
}

/** Ids of every entry on the given dates; used to bound "selected dishes" to the
 *  visible week so an off-screen selection can't silently feed the export. */
export function entryIdsForDates(days: CalendarDay[], dates: string[]): Set<string> {
  const ids = new Set<string>()
  dates.forEach(date => getDay(days, date).recipes.forEach(r => ids.add(r.id)))
  return ids
}
