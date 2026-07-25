import assert from 'node:assert/strict'
import { test } from 'node:test'
import { portionsLabel, servingsAreKnown, servingsWereScaled } from './servings'

// Mirrors the Polish defaults in ui_strings.py; the real UiStrings object is
// passed at runtime, but the label logic must be tested without React.
const UI = {
  portions_label: 'Porcje',
  portions_unknown: 'nieokreślone',
  portions_scaled_from: 'przeliczone z {n}',
}

test('known count renders as a plain number', () => {
  assert.equal(portionsLabel(4, 4, UI), 'Porcje: 4')
})

test('scaled count states what it was converted from', () => {
  // The trust-builder: the user sees the adjustment happened.
  assert.equal(portionsLabel(8, 4, UI), 'Porcje: 8 (przeliczone z 4)')
})

test('no source count means no conversion claim', () => {
  // Nothing was scaled, so claiming a conversion would be a lie.
  assert.equal(portionsLabel(8, undefined, UI), 'Porcje: 8')
  assert.equal(portionsLabel(8, 0, UI), 'Porcje: 8')
})

test('undefined servings render as unknown', () => {
  assert.equal(portionsLabel(undefined, undefined, UI), 'Porcje: nieokreślone')
})

test('zero servings render as unknown, never as "0"', () => {
  // A page that stated no count extracts as 0; "Porcje: 0" would be nonsense.
  assert.equal(portionsLabel(0, undefined, UI), 'Porcje: nieokreślone')
})

test('unknown wins even when a source count exists', () => {
  assert.equal(portionsLabel(0, 4, UI), 'Porcje: nieokreślone')
})

test('servingsAreKnown truth table', () => {
  assert.equal(servingsAreKnown(4), true)
  assert.equal(servingsAreKnown(1), true)
  assert.equal(servingsAreKnown(0), false)
  assert.equal(servingsAreKnown(-1), false)
  assert.equal(servingsAreKnown(undefined), false)
})

test('servingsWereScaled truth table', () => {
  assert.equal(servingsWereScaled(8, 4), true)
  assert.equal(servingsWereScaled(4, 4), false)
  assert.equal(servingsWereScaled(8, undefined), false)
  assert.equal(servingsWereScaled(8, 0), false)
  assert.equal(servingsWereScaled(undefined, 4), false)
  assert.equal(servingsWereScaled(0, 4), false)
})

test('label falls back to Polish defaults when ui strings are absent', () => {
  // UiStrings fields are optional on the wire; a missing key must not render
  // "undefined: 4" to the user.
  assert.equal(portionsLabel(4, 4, {}), 'Porcje: 4')
  assert.equal(portionsLabel(undefined, undefined, {}), 'Porcje: nieokreślone')
  assert.equal(portionsLabel(8, 4, {}), 'Porcje: 8 (przeliczone z 4)')
})
