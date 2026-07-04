import { useState } from 'react'
import { GroceryMatchResult, ShopItem, UiStrings } from '../types'
import { API_BASE } from '../config'
import FriscoPanel from './FriscoPanel'

interface Props {
  items: ShopItem[]
  onChange: (items: ShopItem[]) => void
  ui: UiStrings
}

const SECTION_ORDER = ['warzywa/owoce', 'nabiał', 'mięso/ryby', 'piekarnia', 'suche produkty', 'inne']

export default function ShoppingList({ items, onChange, ui }: Props) {
  const hasSections = items.some(i => i.section)
  const [friscoLoading, setFriscoLoading] = useState(false)
  const [friscoResult, setFriscoResult] = useState<GroceryMatchResult | null>(null)

  async function findInFrisco() {
    const ingredients = items.map(i => i.name).filter(Boolean)
    if (ingredients.length === 0) return
    setFriscoLoading(true)
    try {
      const resp = await fetch(`${API_BASE}/v1/grocery/frisco/match`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ingredients }),
      })
      if (resp.ok) setFriscoResult(await resp.json())
    } catch {
      // best-effort; leave the list as-is on failure
    } finally {
      setFriscoLoading(false)
    }
  }

  function toggle(idx: number) {
    onChange(items.map((item, i) => i === idx ? { ...item, checked: !item.checked } : item))
  }

  function clearChecked() {
    onChange(items.filter(i => !i.checked))
  }

  // Group by section when section data is present
  const sections: { name: string; entries: { item: ShopItem; idx: number }[] }[] = []
  if (hasSections) {
    const sectionMap = new Map<string, { item: ShopItem; idx: number }[]>()
    items.forEach((item, idx) => {
      const sec = item.section ?? 'inne'
      if (!sectionMap.has(sec)) sectionMap.set(sec, [])
      sectionMap.get(sec)!.push({ item, idx })
    })
    const orderedKeys = [
      ...SECTION_ORDER.filter(s => sectionMap.has(s)),
      ...[...sectionMap.keys()].filter(s => !SECTION_ORDER.includes(s)),
    ]
    orderedKeys.forEach(name => sections.push({ name, entries: sectionMap.get(name)! }))
  }

  return (
    <div style={styles.section}>
      <h3 style={styles.sectionTitle}>{ui.shopping_list_heading ?? 'Lista zakupów'}</h3>
      <div style={styles.list}>
        {hasSections ? (
          sections.map(sec => (
            <div key={sec.name}>
              <div style={styles.groupHeader}>{sec.name}</div>
              {sec.entries.map(({ item, idx }) => (
                <label key={idx} style={styles.row}>
                  <input type="checkbox" checked={item.checked} onChange={() => toggle(idx)} style={{ accentColor: '#c0392b' }} />
                  <span style={{ ...(item.checked ? styles.strikethrough : {}) }}>{item.name}</span>
                </label>
              ))}
            </div>
          ))
        ) : (
          items.map((item, idx) => (
            <label key={item.name} style={styles.row}>
              <input type="checkbox" checked={item.checked} onChange={() => toggle(idx)} style={{ accentColor: '#c0392b' }} />
              <span style={{ ...(item.checked ? styles.strikethrough : {}) }}>{item.name}</span>
            </label>
          ))
        )}
      </div>
      <div style={styles.actions}>
        <button style={styles.clearBtn} onClick={clearChecked}>
          {ui.shopping_list_clear ?? 'Wyczyść zaznaczone'}
        </button>
        <button
          style={styles.friscoBtn}
          onClick={findInFrisco}
          disabled={friscoLoading || items.length === 0}
        >
          {friscoLoading ? (ui.frisco_loading ?? 'Szukam w Frisco…') : (ui.frisco_button ?? 'Znajdź w Frisco')}
        </button>
      </div>
      {friscoResult && (
        <FriscoPanel result={friscoResult} ui={ui} onClose={() => setFriscoResult(null)} />
      )}
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  section: { display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden', padding: 14, borderTop: '1px solid #e8e0d8' },
  sectionTitle: { fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: 0.5, color: '#888', marginBottom: 10 },
  list: { flex: 1, overflowY: 'auto', fontSize: '0.84rem', marginBottom: 8 },
  groupHeader: { fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: 0.5, color: '#c0392b', fontWeight: 600, padding: '8px 0 3px', borderBottom: '1px solid #f0e8e0', marginBottom: 2 },
  row: { display: 'flex', alignItems: 'center', gap: 8, padding: '4px 0', borderBottom: '1px solid #f5f0eb', cursor: 'pointer' },
  strikethrough: { textDecoration: 'line-through', color: '#aaa' },
  clearBtn: { background: 'none', border: '1px solid #e8e0d8', borderRadius: 6, padding: '4px 10px', fontSize: '0.78rem', color: '#888', cursor: 'pointer', flexShrink: 0 },
  actions: { display: 'flex', gap: 8, flexShrink: 0, alignItems: 'center' },
  friscoBtn: { background: '#c0392b', border: '1px solid #c0392b', borderRadius: 6, padding: '5px 12px', fontSize: '0.78rem', color: '#fff', cursor: 'pointer', fontWeight: 600 },
}
