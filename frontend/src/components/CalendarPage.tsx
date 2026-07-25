import { useState } from 'react'
import { CalendarDay, CalendarEntry, MealSlot, MEAL_SLOTS, Recipe, ShopItem, UiStrings } from '../types'
import { API_BASE } from '../config'
import {
  DragPayload,
  entriesForSlot,
  entryIdsForDates,
  getDay,
  ingredientsForDates,
  ingredientsForSelection,
  moveEntry,
  removeEntry,
} from '../lib/calendar'
import { portionsLabel, servingsAreKnown } from '../lib/servings'
import { mergeOrganized } from '../lib/shoppingList'
import { authHeaders } from '../hooks/useSpizarnia'
import { t } from '../theme'

const DAYS_PL = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']

// Fallback labels; the server's ui-strings win when present.
const SLOT_LABELS_PL: Record<MealSlot, string> = {
  sniadanie: 'Śniadanie',
  lunch: 'Lunch',
  obiad: 'Obiad',
  kolacja: 'Kolacja',
}

function slotLabel(slot: MealSlot, ui: UiStrings): string {
  switch (slot) {
    case 'sniadanie': return ui.calendar_slot_sniadanie ?? SLOT_LABELS_PL.sniadanie
    case 'lunch':     return ui.calendar_slot_lunch ?? SLOT_LABELS_PL.lunch
    case 'obiad':     return ui.calendar_slot_obiad ?? SLOT_LABELS_PL.obiad
    case 'kolacja':   return ui.calendar_slot_kolacja ?? SLOT_LABELS_PL.kolacja
  }
}

// Polish plural for "danie": 1 danie, 2-4 dania, 5+ dań.
function dishWord(n: number): string {
  if (n === 1) return 'danie'
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 >= 2 && mod10 <= 4 && !(mod100 >= 12 && mod100 <= 14)) return 'dania'
  return 'dań'
}

function getWeekDates(offset: number): string[] {
  const now = new Date()
  const dayOfWeek = (now.getDay() + 6) % 7  // Monday = 0
  const monday = new Date(now)
  monday.setDate(now.getDate() - dayOfWeek + offset * 7)
  return Array.from({ length: 7 }, (_, i) => {
    const d = new Date(monday)
    d.setDate(monday.getDate() + i)
    return d.toISOString().slice(0, 10)
  })
}

function formatDate(iso: string): string {
  const d = new Date(iso)
  return `${d.getDate()}.${d.getMonth() + 1}`
}

// Polish plural for "day": 1 dzień, 2-4 dni, else dni.
function dayWord(n: number): string {
  return n === 1 ? 'dzień' : 'dni'
}

interface Props {
  days: CalendarDay[]
  onChange: (days: CalendarDay[]) => void
  onExportToShoppingList: (items: ShopItem[]) => void
  ui: UiStrings
  // STEP 51 — deduct the pantry from the built list. The server ignores this
  // unless the request also carries a verifiable identity, hence the token.
  subtractPantry: boolean
  idToken: string | null
}

export default function CalendarPage({ days, onChange, onExportToShoppingList, ui, subtractPantry, idToken }: Props) {
  const [weekOffset, setWeekOffset] = useState(0)
  // Drop target under the cursor, keyed "date|slot" so only one slot highlights.
  const [dragOver, setDragOver] = useState<string | null>(null)
  // The whole entry, not just its recipe: the entry carries the authoritative
  // portion counts (STEP 49) — the nested recipe may predate them.
  const [detailEntry, setDetailEntry] = useState<CalendarEntry | null>(null)
  const [exportLoading, setExportLoading] = useState<'days' | 'dishes' | null>(null)
  // Days selected for the shopping list. null = "all days with recipes" (default);
  // once the user touches a checkbox we track an explicit set of ISO dates.
  const [selectedDays, setSelectedDays] = useState<Set<string> | null>(null)
  // Individually ticked dishes — an INDEPENDENT selection from selectedDays,
  // driving the "wybrane dania" export button. Empty by default (opt-in).
  const [selectedEntryIds, setSelectedEntryIds] = useState<Set<string>>(new Set())

  const weekDates = getWeekDates(weekOffset)

  function getDayData(date: string): CalendarDay {
    return getDay(days, date)
  }

  // A day is "selected" for the shopping list when its checkbox is ticked.
  // Default (selectedDays === null): every day in the visible week that has recipes.
  function isDaySelected(date: string): boolean {
    if (selectedDays === null) return getDayData(date).recipes.length > 0
    return selectedDays.has(date)
  }

  function toggleDaySelected(date: string) {
    setSelectedDays(prev => {
      // Materialise the current effective selection, then flip this day.
      const base = prev ?? new Set(weekDates.filter(d => getDayData(d).recipes.length > 0))
      const next = new Set(base)
      if (next.has(date)) next.delete(date)
      else next.add(date)
      return next
    })
  }

  const selectedDates = weekDates.filter(isDaySelected)

  function updateDay(date: string, updater: (d: CalendarDay) => CalendarDay) {
    const existing = days.find(d => d.date === date)
    if (existing) {
      onChange(days.map(d => d.date === date ? updater(d) : d))
    } else {
      onChange([...days, updater({ date, recipes: [], freeText: '' })])
    }
  }

  function removeRecipe(date: string, id: string) {
    onChange(removeEntry(days, date, id))
    // Don't leave a deleted dish ticked — it would count toward the export label.
    setSelectedEntryIds(prev => {
      if (!prev.has(id)) return prev
      const next = new Set(prev)
      next.delete(id)
      return next
    })
  }

  function setFreeText(date: string, text: string) {
    updateDay(date, d => ({ ...d, freeText: text }))
  }

  function toggleEntrySelected(id: string) {
    setSelectedEntryIds(prev => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  // Drop moves the dragged entry into (date, slot) — see moveEntry for why this
  // is a move rather than the copy it used to be.
  function onDrop(e: React.DragEvent, date: string, slot: MealSlot) {
    e.preventDefault()
    setDragOver(null)
    try {
      const payload: DragPayload = JSON.parse(e.dataTransfer.getData('application/json'))
      if (!payload?.entryId) return
      onChange(moveEntry(days, payload, date, slot))
    } catch { /* malformed payload — ignore */ }
  }

  // Shared by both export buttons: hand the ingredients to the ShoppingListAgent,
  // falling back to a naive dedup when the backend is unavailable.
  async function buildList(ingredients: string[], which: 'days' | 'dishes') {
    if (!ingredients.length) return
    setExportLoading(which)
    try {
      const resp = await fetch(`${API_BASE}/v1/shopping-list/build`, {
        method: 'POST',
        // Identity is optional on this route; sending it lets the server
        // subtract the pantry when subtractPantry is on (it ignores it otherwise).
        headers: authHeaders(idToken, { 'Content-Type': 'application/json' }),
        body: JSON.stringify({ ingredients, subtract_pantry: subtractPantry }),
      })
      if (resp.ok) {
        const data = await resp.json()
        // Structured response: { items: [{name, quantity, section, pantry_note}], sections: [...] }
        const items: ShopItem[] = mergeOrganized([], data.items ?? [])
        onExportToShoppingList(items)
      } else {
        const unique = [...new Set(ingredients.map(i => i.toLowerCase()))]
        onExportToShoppingList(unique.map(name => ({ name, checked: false })))
      }
    } catch {
      const unique = [...new Set(ingredients.map(i => i.toLowerCase()))]
      onExportToShoppingList(unique.map(name => ({ name, checked: false })))
    } finally {
      setExportLoading(null)
    }
  }

  const hasAnyRecipes = weekDates.some(d => getDayData(d).recipes.length > 0)
  // The list can only be built from selected days that actually contain recipes.
  const selectedWithRecipes = selectedDates.filter(d => getDayData(d).recipes.length > 0)
  const canExport = selectedWithRecipes.length > 0 && exportLoading === null
  // Ticked dishes are scoped to the visible week, so a selection left behind on
  // another week can't silently feed this export.
  const visibleIds = entryIdsForDates(days, weekDates)
  const selectedVisibleIds = [...selectedEntryIds].filter(id => visibleIds.has(id))
  const canExportDishes = selectedVisibleIds.length > 0 && exportLoading === null

  return (
    <div style={styles.page}>
      {/* Recipe detail modal */}
      {detailEntry?.recipe && (
        <RecipeModal entry={detailEntry} recipe={detailEntry.recipe} ui={ui}
          onClose={() => setDetailEntry(null)} />
      )}

      <div style={styles.toolbar}>
        <button style={styles.navBtn} onClick={() => setWeekOffset(w => w - 1)}>← Poprzedni tydzień</button>
        <span style={styles.weekLabel}>
          {weekOffset === 0 ? 'Bieżący tydzień' : weekOffset === 1 ? 'Następny tydzień' : weekOffset < 0 ? `${Math.abs(weekOffset)} tyg. temu` : `Za ${weekOffset} tyg.`}
          {' '}({formatDate(weekDates[0])} – {formatDate(weekDates[6])})
        </span>
        <button style={styles.navBtn} onClick={() => setWeekOffset(w => w + 1)}>Następny tydzień →</button>
        <button
          style={{ ...styles.exportBtn, ...(!canExport ? styles.exportBtnDisabled : {}) }}
          onClick={() => buildList(ingredientsForDates(days, selectedWithRecipes), 'days')}
          disabled={!canExport}
          title={hasAnyRecipes && !canExport ? 'Zaznacz co najmniej jeden dzień z przepisami' : undefined}
        >
          {exportLoading === 'days'
            ? 'Przetwarzam…'
            : `Utwórz listę zakupów (${selectedWithRecipes.length} ${dayWord(selectedWithRecipes.length)})`}
        </button>
        <button
          style={{ ...styles.exportAltBtn, ...(!canExportDishes ? styles.exportBtnDisabled : {}) }}
          onClick={() => buildList(ingredientsForSelection(days, weekDates, selectedEntryIds), 'dishes')}
          disabled={!canExportDishes}
          title={!canExportDishes ? 'Zaznacz co najmniej jedno danie' : undefined}
        >
          {exportLoading === 'dishes'
            ? 'Przetwarzam…'
            : `${ui.calendar_export_selected ?? 'Utwórz listę zakupów (wybrane dania)'}`
              + (selectedVisibleIds.length > 0 ? ` — ${selectedVisibleIds.length} ${dishWord(selectedVisibleIds.length)}` : '')}
        </button>
      </div>

      <div style={styles.grid}>
        {weekDates.map((date, idx) => {
          const day = getDayData(date)
          const isToday = date === new Date().toISOString().slice(0, 10)
          return (
            <div
              key={date}
              style={{
                ...styles.dayCol,
                ...(isToday ? styles.todayCol : {}),
              }}
            >
              <div style={styles.dayHeader}>
                {day.recipes.length > 0 && (
                  <input
                    type="checkbox"
                    checked={isDaySelected(date)}
                    onChange={() => toggleDaySelected(date)}
                    style={styles.dayCheckbox}
                    title="Uwzględnij ten dzień w liście zakupów"
                    aria-label={`Uwzględnij ${DAYS_PL[idx]} w liście zakupów`}
                  />
                )}
                <span style={styles.dayName}>{DAYS_PL[idx]}</span>
                <span style={styles.dayDate}>{formatDate(date)}</span>
                {isToday && <span style={styles.todayBadge}>Dziś</span>}
              </div>

              <div style={styles.dayBody}>
                {/* Notatki is a section, not a slot: it holds no dishes, so it has
                    no checkbox and is never a drop target. */}
                <div style={styles.slotLabel}>{ui.calendar_notes_label ?? 'Notatki'}</div>
                <textarea
                  style={styles.freeText}
                  placeholder="Notatka…"
                  value={day.freeText}
                  onChange={e => setFreeText(date, e.target.value)}
                  rows={2}
                />

                {MEAL_SLOTS.map(slot => {
                  const slotEntries = entriesForSlot(day, slot)
                  const key = `${date}|${slot}`
                  return (
                    <div
                      key={slot}
                      style={{
                        ...styles.slot,
                        ...(dragOver === key ? styles.slotDragOver : {}),
                      }}
                      onDragOver={e => { e.preventDefault(); e.dataTransfer.dropEffect = 'move'; setDragOver(key) }}
                      onDragLeave={() => setDragOver(prev => (prev === key ? null : prev))}
                      onDrop={e => onDrop(e, date, slot)}
                    >
                      <div style={styles.slotLabel}>{slotLabel(slot, ui)}</div>
                      {slotEntries.length === 0 ? (
                        <div style={styles.slotEmpty}>—</div>
                      ) : (
                        slotEntries.map(entry => (
                          <RecipeChip
                            key={entry.id}
                            entry={entry}
                            date={date}
                            slot={slot}
                            ui={ui}
                            selected={selectedEntryIds.has(entry.id)}
                            onToggleSelected={() => toggleEntrySelected(entry.id)}
                            onRemove={() => removeRecipe(date, entry.id)}
                            onOpen={entry.recipe ? () => setDetailEntry(entry) : undefined}
                          />
                        ))
                      )}
                    </div>
                  )
                })}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

function RecipeChip({ entry, date, slot, ui, selected, onToggleSelected, onRemove, onOpen }: {
  entry: CalendarEntry
  date: string
  slot: MealSlot
  ui: UiStrings
  selected: boolean
  onToggleSelected: () => void
  onRemove: () => void
  onOpen?: () => void
}) {
  // Compact badge so a week of meals is readable without opening every modal.
  // Only shown when the count is real — an unknown count is not worth a badge,
  // the modal explains it.
  const servings = entry.servings ?? entry.recipe?.servings
  const sourceServings = entry.sourceServings ?? entry.recipe?.original_servings
  const showBadge = servingsAreKnown(servings)
  const portions = portionsLabel(servings, sourceServings, ui)
  // Carry only the identity + origin: the drop is a move, so the target needs to
  // know where to remove the entry from, and the entry data is already in state.
  function onDragStart(e: React.DragEvent) {
    const payload: DragPayload = { entryId: entry.id, fromDate: date, fromSlot: slot }
    e.dataTransfer.setData('application/json', JSON.stringify(payload))
    e.dataTransfer.effectAllowed = 'move'
  }

  return (
    <div style={{ ...styles.chip, ...(selected ? styles.chipSelected : {}) }} draggable onDragStart={onDragStart}>
      <input
        type="checkbox"
        checked={selected}
        onChange={onToggleSelected}
        style={styles.chipCheckbox}
        title="Uwzględnij to danie w liście zakupów"
        aria-label={`Uwzględnij ${entry.recipeName} w liście zakupów`}
      />
      <span
        style={{ ...styles.chipName, ...(onOpen ? styles.chipNameClickable : {}) }}
        onClick={onOpen}
        title={`${portions}\n${onOpen ? 'Kliknij, aby zobaczyć przepis' : entry.ingredients.join(', ')}`}
      >
        {entry.recipeName}
      </span>
      {showBadge && (
        <span style={styles.chipPortions} title={portions}>{servings}&nbsp;por.</span>
      )}
      <button style={styles.chipRm} onClick={onRemove} title="Usuń">✕</button>
    </div>
  )
}

function RecipeModal({ entry, recipe, ui, onClose }: {
  entry: CalendarEntry
  recipe: Recipe
  ui: UiStrings
  onClose: () => void
}) {
  // Prefer the entry's counts — they were stamped from the recipe the scaler
  // actually produced. Fall back to the nested recipe for pre-STEP-49 entries.
  const servings = entry.servings ?? recipe.servings
  const sourceServings = entry.sourceServings ?? recipe.original_servings
  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.modal} onClick={e => e.stopPropagation()}>
        {recipe.image_url && (
          <img src={recipe.image_url} alt={recipe.name} style={styles.modalImg}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
        )}
        <div style={styles.modalHeader}>
          <h3 style={styles.modalTitle}>{recipe.name}</h3>
          <button style={styles.modalClose} onClick={onClose}>✕</button>
        </div>
        <div style={styles.modalBody}>
          <p style={styles.modalDesc}>{recipe.description}</p>
          <div style={styles.modalMeta}>
            ⏱ Przygotowanie {recipe.prep_time_minutes} min · Gotowanie {recipe.cook_time_minutes} min · {recipe.difficulty} · {portionsLabel(servings, sourceServings, ui)}
          </div>
          <h4 style={styles.modalSection}>Składniki</h4>
          <ul style={styles.modalList}>
            {recipe.ingredients.map((ing, i) => <li key={i}>{ing}</li>)}
          </ul>
          <h4 style={styles.modalSection}>Kroki</h4>
          <ol style={styles.modalList}>
            {recipe.steps.map((s, i) => <li key={i} style={{ marginBottom: 6 }}>{s}</li>)}
          </ol>
          {recipe.tips && recipe.tips.length > 0 && (
            <>
              <h4 style={styles.modalSection}>Wskazówki</h4>
              <ul style={styles.modalList}>
                {recipe.tips.map((t, i) => <li key={i}>{t}</li>)}
              </ul>
            </>
          )}
          {recipe.source_url && (
            <div style={{ marginTop: 14 }}>
              <a href={recipe.source_url} target="_blank" rel="noopener noreferrer" style={styles.modalSourceLink}>
                Źródło przepisu ↗
              </a>
            </div>
          )}
        </div>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  page: { display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden', padding: 18, gap: 14, background: t.color.bg },

  toolbar: { display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, flexWrap: 'wrap' },
  navBtn: { background: t.color.surface, border: `1px solid ${t.color.border}`, borderRadius: t.radius.md, padding: '7px 14px', fontSize: '0.82rem', fontWeight: 500, color: t.color.text, cursor: 'pointer' },
  weekLabel: { flex: 1, textAlign: 'center', fontWeight: 600, fontSize: '0.92rem', color: t.color.text },
  exportBtn: { background: t.color.primary, color: '#fff', border: 'none', borderRadius: t.radius.md, padding: '8px 16px', fontSize: '0.82rem', fontWeight: 600, cursor: 'pointer', boxShadow: t.shadow.sm },
  // Secondary treatment: same action, narrower (per-dish) selection.
  exportAltBtn: { background: t.color.surface, color: t.color.primary, border: `1px solid ${t.color.primary}`, borderRadius: t.radius.md, padding: '8px 16px', fontSize: '0.82rem', fontWeight: 600, cursor: 'pointer' },
  exportBtnDisabled: { opacity: 0.45, cursor: 'default', boxShadow: 'none' },

  grid: { display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 10, flex: 1, overflow: 'hidden' },

  dayCol: {
    background: t.color.surface,
    border: `1px solid ${t.color.border}`,
    borderRadius: t.radius.lg,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    transition: 'border-color 0.15s, box-shadow 0.15s',
    minHeight: 0,
    boxShadow: t.shadow.sm,
  },
  todayCol: { borderColor: t.color.primary, borderWidth: 2, boxShadow: t.shadow.md },

  dayHeader: {
    background: t.color.surfaceMuted,
    padding: '9px 11px',
    borderBottom: `1px solid ${t.color.border}`,
    display: 'flex',
    alignItems: 'center',
    gap: 6,
    flexShrink: 0,
  },
  dayCheckbox: { accentColor: t.color.primary, cursor: 'pointer', margin: 0 },
  dayName: { fontWeight: 600, fontSize: '0.8rem', color: t.color.text },
  dayDate: { fontSize: '0.75rem', color: t.color.textMuted },
  todayBadge: { background: t.color.primary, color: '#fff', borderRadius: t.radius.sm, padding: '2px 7px', fontSize: '0.66rem', fontWeight: 600, marginLeft: 'auto' },

  dayBody: { flex: 1, overflowY: 'auto', padding: '9px 11px', display: 'flex', flexDirection: 'column', gap: 6 },

  // One meal section. Empty slots stay a thin labelled strip so five sections
  // fit in a 1/7-width column without the day becoming unreadable.
  slot: {
    display: 'flex',
    flexDirection: 'column',
    gap: 4,
    padding: '4px 5px',
    borderRadius: t.radius.sm,
    border: '1px dashed transparent',
    transition: 'background 0.12s, border-color 0.12s',
  },
  slotDragOver: { borderColor: t.color.success, background: t.color.successSoft },
  slotLabel: {
    fontSize: '0.64rem',
    textTransform: 'uppercase',
    letterSpacing: 0.5,
    fontWeight: 700,
    color: t.color.textMuted,
  },
  slotEmpty: { color: t.color.textFaint, fontSize: '0.7rem', padding: '1px 0 3px' },

  chip: {
    background: t.color.primarySoft,
    border: `1px solid ${t.color.primaryBorder}`,
    borderRadius: t.radius.sm,
    padding: '5px 9px',
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    fontSize: '0.78rem',
    cursor: 'grab',
    color: t.color.primaryText,
  },
  chipSelected: { borderColor: t.color.primary, boxShadow: `0 0 0 1px ${t.color.primary}` },
  chipCheckbox: { accentColor: t.color.primary, cursor: 'pointer', margin: 0, flexShrink: 0 },
  chipName: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  chipNameClickable: { cursor: 'pointer', textDecoration: 'underline', textDecorationStyle: 'dotted', color: t.color.primary },
  // Portion badge (STEP 49) — muted, never competing with the dish name.
  chipPortions: { flexShrink: 0, fontSize: '0.68rem', color: t.color.textMuted, background: t.color.bg, border: `1px solid ${t.color.border}`, borderRadius: t.radius.sm, padding: '1px 5px', lineHeight: 1.4 },
  chipRm: { background: 'none', border: 'none', color: t.color.textFaint, cursor: 'pointer', fontSize: '0.75rem', padding: 0, lineHeight: 1 },

  freeText: {
    border: `1px solid ${t.color.border}`,
    borderRadius: t.radius.sm,
    fontSize: '0.78rem',
    padding: '5px 7px',
    resize: 'none',
    fontFamily: 'inherit',
    color: t.color.textMuted,
    background: 'transparent',
    outline: 'none',
  },

  // Modal
  overlay: {
    position: 'fixed', inset: 0,
    background: 'rgba(15, 23, 42, 0.55)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 1000,
  },
  modal: {
    background: t.color.surface,
    borderRadius: t.radius.xl,
    width: '90%',
    maxWidth: 600,
    maxHeight: '85vh',
    display: 'flex',
    flexDirection: 'column',
    boxShadow: t.shadow.lg,
    overflow: 'hidden',
  },
  modalImg: { width: '100%', height: 200, objectFit: 'cover', display: 'block', flexShrink: 0 },
  modalHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '16px 20px',
    borderBottom: `1px solid ${t.color.border}`,
    flexShrink: 0,
  },
  modalTitle: { margin: 0, fontSize: '1.15rem', fontWeight: 600, color: t.color.text },
  modalClose: {
    background: 'none', border: 'none', fontSize: '1.1rem',
    cursor: 'pointer', color: t.color.textMuted, padding: '0 4px',
  },
  modalBody: { overflowY: 'auto', padding: '16px 20px', fontSize: '0.88rem', lineHeight: 1.6, color: t.color.text },
  modalDesc: { color: t.color.textMuted, marginBottom: 8 },
  modalMeta: { color: t.color.textFaint, fontSize: '0.78rem', marginBottom: 14 },
  modalSection: { fontSize: '0.85rem', fontWeight: 600, marginTop: 14, marginBottom: 6, color: t.color.text },
  modalList: { paddingLeft: 20, margin: 0 },
  modalSourceLink: { fontSize: '0.82rem', color: t.color.primary, textDecoration: 'none', borderBottom: `1px dotted ${t.color.primary}` },
}
