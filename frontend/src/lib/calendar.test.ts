import assert from 'node:assert/strict'
import { test } from 'node:test'
import { CalendarDay, CalendarEntry } from '../types'
import {
  entriesForSlot,
  entryIdsForDates,
  ingredientsForDates,
  ingredientsForSelection,
  moveEntry,
  removeEntry,
  slotOf,
} from './calendar'

function entry(id: string, over: Partial<CalendarEntry> = {}): CalendarEntry {
  return { id, recipeName: `dish-${id}`, ingredients: [`ing-${id}`], ...over }
}

const MON = '2026-08-10'
const TUE = '2026-08-11'

function days(): CalendarDay[] {
  return [
    {
      date: MON,
      freeText: 'notatka',
      recipes: [
        entry('a', { mealSlot: 'sniadanie' }),
        entry('b', { mealSlot: 'obiad' }),
        entry('c'),  // legacy: no slot
      ],
    },
    { date: TUE, freeText: '', recipes: [entry('d', { mealSlot: 'kolacja' })] },
  ]
}

test('slotOf falls back to obiad for entries saved before meal slots existed', () => {
  assert.equal(slotOf(entry('x')), 'obiad')
  assert.equal(slotOf(entry('x', { mealSlot: 'lunch' })), 'lunch')
})

test('entriesForSlot groups by slot, counting legacy entries as obiad', () => {
  const mon = days()[0]
  assert.deepEqual(entriesForSlot(mon, 'sniadanie').map(e => e.id), ['a'])
  assert.deepEqual(entriesForSlot(mon, 'obiad').map(e => e.id), ['b', 'c'])
  assert.deepEqual(entriesForSlot(mon, 'lunch'), [])
})

test('moveEntry moves between slots within a day without duplicating', () => {
  const next = moveEntry(days(), { entryId: 'a', fromDate: MON, fromSlot: 'sniadanie' }, MON, 'kolacja')
  const mon = next.find(d => d.date === MON)!
  assert.equal(mon.recipes.filter(r => r.id === 'a').length, 1)
  assert.equal(slotOf(mon.recipes.find(r => r.id === 'a')!), 'kolacja')
  assert.deepEqual(entriesForSlot(mon, 'sniadanie'), [])
})

test('moveEntry moves across days, removing it from the source day', () => {
  const next = moveEntry(days(), { entryId: 'a', fromDate: MON, fromSlot: 'sniadanie' }, TUE, 'lunch')
  assert.deepEqual(next.find(d => d.date === MON)!.recipes.map(r => r.id), ['b', 'c'])
  const moved = next.find(d => d.date === TUE)!.recipes.find(r => r.id === 'a')!
  assert.equal(slotOf(moved), 'lunch')
  assert.equal(moved.date, TUE)
})

test('moveEntry creates the target day when it does not exist yet', () => {
  const wed = '2026-08-12'
  const next = moveEntry(days(), { entryId: 'a', fromDate: MON, fromSlot: 'sniadanie' }, wed, 'obiad')
  assert.deepEqual(next.find(d => d.date === wed)!.recipes.map(r => r.id), ['a'])
})

test('moveEntry onto its own slot is a no-op (does not reorder)', () => {
  const before = days()
  assert.equal(moveEntry(before, { entryId: 'b', fromDate: MON, fromSlot: 'obiad' }, MON, 'obiad'), before)
})

test('moveEntry ignores a stale drag whose entry no longer exists', () => {
  const before = days()
  assert.equal(moveEntry(before, { entryId: 'gone', fromDate: MON, fromSlot: 'obiad' }, TUE, 'lunch'), before)
})

test('removeEntry removes only the targeted entry on the targeted day', () => {
  const next = removeEntry(days(), MON, 'b')
  assert.deepEqual(next.find(d => d.date === MON)!.recipes.map(r => r.id), ['a', 'c'])
  assert.deepEqual(next.find(d => d.date === TUE)!.recipes.map(r => r.id), ['d'])
})

test('ingredientsForSelection collects ingredients from ticked dishes only', () => {
  assert.deepEqual(ingredientsForSelection(days(), [MON, TUE], new Set(['a', 'd'])), ['ing-a', 'ing-d'])
  assert.deepEqual(ingredientsForSelection(days(), [MON, TUE], new Set()), [])
})

test('ingredientsForSelection ignores ticked dishes outside the given dates', () => {
  assert.deepEqual(ingredientsForSelection(days(), [TUE], new Set(['a', 'd'])), ['ing-d'])
})

test('ingredientsForSelection orders by day then slot, not by insertion', () => {
  // 'c' is legacy (obiad) and 'a' is sniadanie — sniadanie comes first.
  assert.deepEqual(ingredientsForSelection(days(), [MON], new Set(['c', 'a'])), ['ing-a', 'ing-c'])
})

test('ingredientsForDates takes every dish on the given days regardless of slot', () => {
  assert.deepEqual(ingredientsForDates(days(), [MON]), ['ing-a', 'ing-b', 'ing-c'])
})

test('entryIdsForDates collects the ids visible on the given dates', () => {
  assert.deepEqual(entryIdsForDates(days(), [MON]), new Set(['a', 'b', 'c']))
})
