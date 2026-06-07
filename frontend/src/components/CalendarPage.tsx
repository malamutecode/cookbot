import { useState } from 'react'
import { CalendarDay, CalendarEntry, Recipe, ShopItem } from '../types'
import { API_BASE } from '../config'

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

  const weekDates = getWeekDates(weekOffset)

  function getDayData(date: string): CalendarDay {
    return days.find(d => d.date === date) ?? { date, recipes: [], freeText: '' }
  }

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
    const allIngredients = weekDates.flatMap(date =>
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
          style={{ ...styles.exportBtn, ...(!hasAnyRecipes || exportLoading ? styles.exportBtnDisabled : {}) }}
          onClick={exportShoppingList}
          disabled={!hasAnyRecipes || exportLoading}
        >
          {exportLoading ? 'Przetwarzam…' : 'Utwórz listę zakupów dla tygodnia'}
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
  page: { display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden', padding: 16, gap: 12, background: '#fffdf8' },

  toolbar: { display: 'flex', alignItems: 'center', gap: 12, flexShrink: 0, flexWrap: 'wrap' },
  navBtn: { background: '#fff', border: '1px solid #e8e0d8', borderRadius: 6, padding: '5px 12px', fontSize: '0.82rem', cursor: 'pointer' },
  weekLabel: { flex: 1, textAlign: 'center', fontWeight: 500, fontSize: '0.9rem', color: '#444' },
  exportBtn: { background: '#27ae60', color: '#fff', border: 'none', borderRadius: 6, padding: '7px 14px', fontSize: '0.82rem', cursor: 'pointer' },
  exportBtnDisabled: { opacity: 0.45, cursor: 'default' },

  grid: { display: 'grid', gridTemplateColumns: 'repeat(7, 1fr)', gap: 8, flex: 1, overflow: 'hidden' },

  dayCol: {
    background: '#fff',
    border: '1px solid #e8e0d8',
    borderRadius: 10,
    display: 'flex',
    flexDirection: 'column',
    overflow: 'hidden',
    transition: 'border-color 0.15s',
    minHeight: 0,
  },
  todayCol: { borderColor: '#c0392b', borderWidth: 2 },
  dragOverCol: { borderColor: '#27ae60', background: '#f0fff4' },

  dayHeader: {
    background: '#f9f5f0',
    padding: '8px 10px',
    borderBottom: '1px solid #e8e0d8',
    display: 'flex',
    alignItems: 'baseline',
    gap: 6,
    flexShrink: 0,
  },
  dayName: { fontWeight: 600, fontSize: '0.8rem', color: '#333' },
  dayDate: { fontSize: '0.75rem', color: '#888' },
  todayBadge: { background: '#c0392b', color: '#fff', borderRadius: 4, padding: '1px 6px', fontSize: '0.68rem', marginLeft: 'auto' },

  dayBody: { flex: 1, overflowY: 'auto', padding: '8px 10px', display: 'flex', flexDirection: 'column', gap: 6 },
  dropHint: { color: '#bbb', fontSize: '0.75rem', textAlign: 'center', padding: '8px 4px', flexShrink: 0 },

  chip: {
    background: '#fff3e0',
    border: '1px solid #f0dcc0',
    borderRadius: 6,
    padding: '4px 8px',
    display: 'flex',
    alignItems: 'center',
    gap: 4,
    fontSize: '0.78rem',
    cursor: 'grab',
  },
  chipName: { flex: 1, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  chipNameClickable: { cursor: 'pointer', textDecoration: 'underline', textDecorationStyle: 'dotted', color: '#c0392b' },
  chipRm: { background: 'none', border: 'none', color: '#aaa', cursor: 'pointer', fontSize: '0.75rem', padding: 0, lineHeight: 1 },

  freeText: {
    border: '1px solid #e8e0d8',
    borderRadius: 6,
    fontSize: '0.78rem',
    padding: '4px 6px',
    resize: 'none',
    fontFamily: 'inherit',
    color: '#555',
    background: 'transparent',
  },

  // Modal
  overlay: {
    position: 'fixed', inset: 0,
    background: 'rgba(0,0,0,0.45)',
    display: 'flex', alignItems: 'center', justifyContent: 'center',
    zIndex: 1000,
  },
  modal: {
    background: '#fff',
    borderRadius: 14,
    width: '90%',
    maxWidth: 600,
    maxHeight: '85vh',
    display: 'flex',
    flexDirection: 'column',
    boxShadow: '0 8px 40px rgba(0,0,0,0.2)',
    overflow: 'hidden',
  },
  modalImg: { width: '100%', height: 200, objectFit: 'cover', display: 'block', flexShrink: 0 },
  modalHeader: {
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'space-between',
    padding: '16px 20px',
    borderBottom: '1px solid #e8e0d8',
    flexShrink: 0,
  },
  modalTitle: { margin: 0, fontSize: '1.1rem', color: '#2d2d2d' },
  modalClose: {
    background: 'none', border: 'none', fontSize: '1.1rem',
    cursor: 'pointer', color: '#888', padding: '0 4px',
  },
  modalBody: { overflowY: 'auto', padding: '16px 20px', fontSize: '0.88rem', lineHeight: 1.6 },
  modalDesc: { color: '#555', marginBottom: 8 },
  modalMeta: { color: '#888', fontSize: '0.78rem', marginBottom: 14 },
  modalSection: { fontSize: '0.85rem', marginTop: 14, marginBottom: 6, color: '#333' },
  modalList: { paddingLeft: 20, margin: 0 },
  modalSourceLink: { fontSize: '0.82rem', color: '#2980b9', textDecoration: 'none', borderBottom: '1px dotted #2980b9' },
}
