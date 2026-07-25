/**
 * Portion-count display rules (STEP 49).
 *
 * Mirrors `servings_are_known` / `servings_were_scaled` in
 * packages/cookbot-core/cookbot/models/calendar.py. Kept in one module so the
 * chat recipe card and the calendar modal cannot drift — a portion count that
 * reads differently in two places is exactly what erodes trust in the number.
 */

/** Only the fields this module needs, all optional (UiStrings arrives over the wire). */
export interface ServingsStrings {
  portions_label?: string
  portions_unknown?: string
  portions_scaled_from?: string   // uses {n}
}

const FALLBACK = {
  label: 'Porcje',
  unknown: 'nieokreślone',
  scaledFrom: 'przeliczone z {n}',
}

/**
 * True when a portion count is real and safe to display.
 *
 * `0` is what the extractor records when a page never stated a serving count —
 * `scale_recipe_to_servings` treats it as having no anchor and skips scaling — so
 * it must read as unknown rather than rendering "Porcje: 0".
 */
export function servingsAreKnown(servings?: number | null): boolean {
  return servings !== undefined && servings !== null && servings > 0
}

/** True when quantities were converted from a different, known source count. */
export function servingsWereScaled(servings?: number | null, sourceServings?: number | null): boolean {
  if (!servingsAreKnown(servings) || !servingsAreKnown(sourceServings)) return false
  return servings !== sourceServings
}

/**
 * The user-facing portion line, in one of three states:
 *
 *   known, unscaled → "Porcje: 4"
 *   known, scaled   → "Porcje: 8 (przeliczone z 4)"
 *   unknown         → "Porcje: nieokreślone"
 *
 * The scaled form is the point of the feature: it shows the adjustment happened
 * rather than asking the user to take the number on faith.
 */
export function portionsLabel(
  servings: number | undefined | null,
  sourceServings: number | undefined | null,
  ui: ServingsStrings,
): string {
  const label = ui.portions_label ?? FALLBACK.label

  if (!servingsAreKnown(servings)) {
    return `${label}: ${ui.portions_unknown ?? FALLBACK.unknown}`
  }
  if (servingsWereScaled(servings, sourceServings)) {
    const from = (ui.portions_scaled_from ?? FALLBACK.scaledFrom).replace('{n}', String(sourceServings))
    return `${label}: ${servings} (${from})`
  }
  return `${label}: ${servings}`
}
