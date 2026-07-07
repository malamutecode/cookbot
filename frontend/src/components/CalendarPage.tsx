import { useState } from 'react'
import { CalendarDay, CalendarEntry, Recipe, ShopItem } from '../types'
import { API_BASE } from '../config'
import { t } from '../theme'

const DAYS_PL = ['Poniedziałek', 'Wtorek', 'Środa', 'Czwartek', 'Piątek', 'Sobota', 'Niedziela']

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
}

export default function CalendarPage({ days, onChange, onExportToShoppingList }: Props) {
  const [weekOffset, setWeekOffset] = useState(0)
  const [dragOver, setDragOver] = useState<string | null>(null)
  const [detailRecipe, setDetailRecipe] = useState<Recipe | null>(null)
  const [exportLoading, setExportLoading] = useState(false)
  // Days selected for the shopping list. null = "all days with recipes" (default);
  // once the user touches a checkbox we track an explicit set of ISO dates.
  const [selectedDays, setSelectedDays] = useState<Set<string> | null>(null)

  const weekDates = getWeekDates(weekOffset)

  if (typeof console !== 'undefined') {
    console.debug('[CAL] CalendarPage render — week:', weekDates[0], '→', weekDates[6],
      '| days in state:', days.map(d => `${d.date}(${d.recipes.length})`).join(', ') || '(none)')
  }

  function getDayData(date: string): CalendarDay {
    return days.find(d => d.date === date) ?? { date, recipes: [], freeText: '' }
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
    updateDay(date, d => ({ ...d, recipes: d.recipes.filter(r => r.id !== id) }))
  }

  function setFreeText(date: string, text: string) {
    updateDay(date, d => ({ ...d, freeText: text }))
  }

  function onDrop(e: React.DragEvent, date: string) {
    e.preventDefault()
    setDragOver(null)
    try {
      const entry: CalendarEntry = JSON.parse(e.dataTransfer.getData('application/json'))
      updateDay(date, d => {
        if (d.recipes.some(r => r.id === entry.id)) return d
        return { ...d, recipes: [...d.recipes, entry] }
      })
    } catch { /* ignore */ }
  }

  async function exportShoppingList() {
    const allIngredients = selectedDates.flatMap(date =>
      getDayData(date).recipes.flatMap(r => r.ingredients)
    )
    if (!allIngredients.length) return
    setExportLoading(true)
    try {
      const resp = await fetch(`${API_BASE}/v1/shopping-list/build`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ingredients: allIngredients }),
      })
      if (resp.ok) {
        const data = await resp.json()
        // Structured response: { items: [{name, quantity, section}], sections: [...] }
        const items: ShopItem[] = (data.items ?? []).map((i: { name: string; quantity: string; section: string }) => ({
          name: i.quantity ? `${i.name} — ${i.quantity}` : i.name,
          checked: false,
          section: i.section,
        }))
        onExportToShoppingList(items)
      } else {
        // Fallback: naive dedup if backend unavailable
        const unique = [...new Set(allIngredients.map(i => i.toLowerCase()))]
        onExportToShoppingList(unique.map(name => ({ name, checked: false })))
      }
    } catch {
      const unique = [...new Set(allIngredients.map(i => i.toLowerCase()))]
      onExportToShoppingList(unique.map(name => ({ name, checked: false })))
    } finally {
      setExportLoading(false)
    }
  }

  const hasAnyRecipes = weekDates.some(d => getDayData(d).recipes.length > 0)
  // The list can only be built from selected days that actually contain recipes.
  const selectedWithRecipes = selectedDates.filter(d => getDayData(d).recipes.length > 0)
  const canExport = selectedWithRecipes.length > 0 && !exportLoading

  return (
    <div style={styles.page}>
      {/* Recipe detail modal */}
      {detailRecipe && (
        <RecipeModal recipe={detailRecipe} onClose={() => setDetailRecipe(null)} />
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
          onClick={exportShoppingList}
          disabled={!canExport}
          title={hasAnyRecipes && !canExport ? 'Zaznacz co najmniej jeden dzień z przepisami' : undefined}
        >
          {exportLoading
            ? 'Przetwarzam…'
            : `Utwórz listę zakupów (${selectedWithRecipes.length} ${dayWord(selectedWithRecipes.length)})`}
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
                ...(dragOver === date ? styles.dragOverCol : {}),
              }}
              onDragOver={e => { e.preventDefault(); setDragOver(date) }}
              onDragLeave={() => setDragOver(null)}
              onDrop={e => onDrop(e, date)}
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
                {day.recipes.map(entry => (
                  <RecipeChip
                    key={entry.id}
                    entry={entry}
                    onRemove={() => removeRecipe(date, entry.id)}
                    onOpen={entry.recipe ? () => setDetailRecipe(entry.recipe!) : undefined}
                  />
                ))}

                <textarea
                  style={styles.freeText}
                  placeholder="Notatka…"
                  value={day.freeText}
                  onChange={e => setFreeText(date, e.target.value)}
                  rows={2}
                />
              </div>

              {day.recipes.length === 0 && !day.freeText && (
                <div style={styles.dropHint}>Przeciągnij przepis tutaj</div>
              )}
            </div>
          )
        })}
      </div>
    </div>
  )
}

function RecipeChip({ entry, onRemove, onOpen }: {
  entry: CalendarEntry
  onRemove: () => void
  onOpen?: () => void
}) {
  function onDragStart(e: React.DragEvent) {
    e.dataTransfer.setData('application/json', JSON.stringify(entry))
    e.dataTransfer.effectAllowed = 'copy'
  }

  return (
    <div style={styles.chip} draggable onDragStart={onDragStart}>
      <span
        style={{ ...styles.chipName, ...(onOpen ? styles.chipNameClickable : {}) }}
        onClick={onOpen}
        title={onOpen ? 'Kliknij, aby zobaczyć przepis' : entry.ingredients.join(', ')}
      >
        {entry.recipeName}
      </span>
      <button style={styles.chipRm} onClick={onRemove} title="Usuń">✕</button>
    </div>
  )
}

function RecipeModal({ recipe, onClose }: { recipe: Recipe; onClose: () => void }) {
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
            ⏱ Przygotowanie {recipe.prep_time_minutes} min · Gotowanie {recipe.cook_time_minutes} min · {recipe.difficulty} · Porcje: {recipe.servings}
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
  dragOverCol: { borderColor: t.color.success, background: t.color.successSoft },

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
  dropHint: { color: t.color.textFaint, fontSize: '0.75rem', textAlign: 'center', padding: '8px 4px', flexShrink: 0 },

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
  chipName: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  chipNameClickable: { cursor: 'pointer', textDecoration: 'underline', textDecorationStyle: 'dotted', color: t.color.primary },
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
