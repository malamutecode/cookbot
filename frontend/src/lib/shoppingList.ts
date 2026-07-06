import { ShopItem } from '../types'

export interface OrganizedItem {
  name: string
  quantity: string
  section: string
}

// Mirrors SECTIONS_ORDER in cookbot/agents/shopping_list.py (store-aisle order).
export const SECTION_ORDER = [
  'warzywa/owoce',
  'nabiał i jaja',
  'mięso/ryby/wędliny',
  'pieczywo',
  'mrożonki',
  'produkty suche/sypkie',
  'napoje',
  'słodycze/przekąski',
  'chemia/dom',
  'higiena/kosmetyki',
  'inne',
]

/**
 * Turn the organizer's response into the new shopping-list items.
 *
 * The ShoppingListAgent keeps every input item (verified), and it may rename or
 * normalize them ("mrożone truskawki 1 kg" → "mrożone truskawki" / "1000 g"). So
 * we TRUST its output and do not try to reconcile it against the previous list by
 * name — that name-matching produced duplicates (an item appeared both in its real
 * aisle and again in "inne"). The only guard is: if the organizer returns nothing,
 * keep the current list unchanged rather than wiping it.
 */
export function mergeOrganized(current: ShopItem[], organized: OrganizedItem[]): ShopItem[] {
  if (!organized || organized.length === 0) return current
  return organized.map(i => ({
    name: i.quantity ? `${i.name} — ${i.quantity}` : i.name,
    checked: false,
    section: i.section,
  }))
}

/** Plain-text render for export — paste-safe (no markdown), grouped by section. */
export function renderListText(items: ShopItem[], heading: string): string {
  if (items.length === 0) return ''
  const hasSections = items.some(i => i.section)
  const lines: string[] = [heading, '']
  if (hasSections) {
    const bySection = new Map<string, ShopItem[]>()
    items.forEach(i => {
      const sec = i.section ?? 'inne'
      if (!bySection.has(sec)) bySection.set(sec, [])
      bySection.get(sec)!.push(i)
    })
    const ordered = [
      ...SECTION_ORDER.filter(s => bySection.has(s)),
      ...[...bySection.keys()].filter(s => !SECTION_ORDER.includes(s)),
    ]
    ordered.forEach(sec => {
      lines.push(`${sec}:`)
      bySection.get(sec)!.forEach(i => lines.push(`- ${i.name}`))
      lines.push('')
    })
  } else {
    items.forEach(i => lines.push(`- ${i.name}`))
  }
  return lines.join('\n').trim()
}
