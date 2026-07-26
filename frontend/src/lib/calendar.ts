import { CalendarDay, CalendarEntry, DEFAULT_MEAL_SLOT, MealSlot, MEAL_SLOTS } from '../types'

/** One entry as the server stores it (`cookbot.models.calendar.CalendarEntry`):
 *  flat, snake_case, and carrying its own date. */
export interface ServerCalendarEntry {
  id: string
  date: string
  recipe_name: string
  ingredients: string[]
  recipe?: unknown
  meal_slot?: MealSlot
  servings?: number | null
  source_servings?: number | null
}

/**
 * Server `CalendarState.entries` → the nested `CalendarDay[]` the UI renders.
 *
 * The two shapes differ on purpose: the server keeps a flat list (one document,
 * trivially appended to), the grid needs day buckets. Entries are grouped by
 * date in first-seen order, and `freeText` starts empty because the server does
 * not store day notes.
 */
export function daysFromServer(entries: ServerCalendarEntry[]): CalendarDay[] {
  const byDate = new Map<string, CalendarDay>()
  for (const e of entries) {
    const day = byDate.get(e.date) ?? { date: e.date, recipes: [], freeText: '' }
    day.recipes.push({
      id: e.id,
      recipeName: e.recipe_name,
      ingredients: e.ingredients ?? [],
      date: e.date,
      recipe: (e.recipe ?? undefined) as CalendarEntry['recipe'],
      mealSlot: e.meal_slot ?? DEFAULT_MEAL_SLOT,
      servings: e.servings ?? undefined,
      sourceServings: e.source_servings ?? undefined,
    })
    byDate.set(e.date, day)
  }
  return [...byDate.values()]
}

/** The inverse: `CalendarDay[]` → the flat entries a PUT /v1/calendar body needs.
 *  The day's own date wins over any stale `entry.date` — a drag moves the entry
 *  between day buckets, and this is what makes that move persist. */
export function daysToServer(days: CalendarDay[]): ServerCalendarEntry[] {
  return days.flatMap(day =>
    day.recipes.map(r => ({
      id: r.id,
      date: day.date,
      recipe_name: r.recipeName,
      ingredients: r.ingredients,
      recipe: r.recipe,
      meal_slot: slotOf(r),
      servings: r.servings ?? null,
      source_servings: r.sourceServings ?? null,
    })),
  )
}

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
