import assert from 'node:assert/strict'
import { test } from 'node:test'
import { mergeOrganized, renderListText } from './shoppingList'
import { ShopItem } from '../types'

test('mergeOrganized trusts the organizer output (no duplicate-in-inne bug)', () => {
  // Regression: the organizer renames/normalizes items
  //   "mrożone truskawki 1 kg" -> name "mrożone truskawki", qty "1000 g", mrożonki
  //   "parmezan 80 ml"         -> name "parmezan",          qty "80 ml", produkty…
  // The old name-matching safety net failed to match the renamed items and
  // re-added the originals to "inne", producing a duplicate in a second category.
  const current: ShopItem[] = [
    { name: 'mrożone truskawki 1 kg', checked: false },
    { name: 'parmezan 80 ml', checked: false },
  ]
  const organized = [
    { name: 'mrożone truskawki', quantity: '1000 g', section: 'mrożonki' },
    { name: 'parmezan', quantity: '80 ml', section: 'produkty suche/sypkie' },
  ]

  const result = mergeOrganized(current, organized)

  // Exactly two items, each in ONE section, nothing duplicated into "inne".
  assert.equal(result.length, 2)
  assert.equal(result.filter(i => i.section === 'inne').length, 0)

  const berries = result.find(i => i.name.startsWith('mrożone truskawki'))
  assert.ok(berries)
  assert.equal(berries!.section, 'mrożonki')
  assert.equal(berries!.name, 'mrożone truskawki — 1000 g')

  const parm = result.find(i => i.name.startsWith('parmezan'))
  assert.ok(parm)
  assert.equal(parm!.section, 'produkty suche/sypkie')
})

test('mergeOrganized keeps the current list when organizer returns nothing', () => {
  const current: ShopItem[] = [{ name: 'cebula', checked: true }]
  assert.deepEqual(mergeOrganized(current, []), current)
})

test('renderListText groups by section, paste-safe plain text', () => {
  const items: ShopItem[] = [
    { name: 'cebula — 2 szt.', checked: false, section: 'warzywa/owoce' },
    { name: 'papier toaletowy', checked: false, section: 'chemia/dom' },
  ]
  const text = renderListText(items, 'Lista zakupów')
  assert.ok(text.includes('warzywa/owoce:'))
  assert.ok(text.includes('- cebula — 2 szt.'))
  assert.ok(text.includes('chemia/dom:'))
  assert.ok(text.includes('- papier toaletowy'))
  // No markdown/HTML artifacts.
  assert.ok(!text.includes('**') && !text.includes('<'))
})

// ── Pantry tag (STEP 51) ──────────────────────────────────────────────────────
// The tag is a real field, never a suffix on `name`: the copied text and the
// Frisco lookup both read `name` verbatim, so folding it in would corrupt them.

test('mergeOrganized carries pantry_note through as pantryNote', () => {
  const organized = [
    { name: 'mąka', quantity: '300 g', section: 'produkty suche/sypkie', pantry_note: 'masz w spiżarni' },
    { name: 'kurczak', quantity: '1 kg', section: 'mięso/ryby/wędliny' },
  ]
  const result = mergeOrganized([], organized)

  const flour = result.find(i => i.name.startsWith('mąka'))!
  assert.equal(flour.pantryNote, 'masz w spiżarni')
  // The name is untouched — the tag never leaks into it.
  assert.equal(flour.name, 'mąka — 300 g')

  // An untagged item stays shaped exactly as before (no empty-string key).
  const chicken = result.find(i => i.name.startsWith('kurczak'))!
  assert.equal(chicken.pantryNote, undefined)
})

test('renderListText appends the pantry tag in parentheses', () => {
  const items: ShopItem[] = [
    { name: 'mąka — 300 g', checked: false, section: 'produkty suche/sypkie', pantryNote: 'masz w spiżarni' },
    { name: 'kurczak — 1 kg', checked: false, section: 'mięso/ryby/wędliny' },
  ]
  const text = renderListText(items, 'Lista zakupów')
  assert.ok(text.includes('- mąka — 300 g (masz w spiżarni)'))
  // Untagged lines are unchanged — no stray parentheses.
  assert.ok(text.includes('- kurczak — 1 kg'))
  assert.ok(!text.includes('kurczak — 1 kg ('))
})

test('renderListText appends the pantry tag in the section-less branch too', () => {
  const items: ShopItem[] = [
    { name: 'mąka', checked: false, pantryNote: 'sprawdź w spiżarni' },
  ]
  const text = renderListText(items, 'Lista zakupów')
  assert.ok(text.includes('- mąka (sprawdź w spiżarni)'))
})
