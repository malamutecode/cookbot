import { useState } from 'react'
import { GroceryMatchResult, ShopItem, UiStrings } from '../types'
import { API_BASE } from '../config'
import FriscoPanel from './FriscoPanel'
import { SECTION_ORDER, mergeOrganized, renderListText } from '../lib/shoppingList'

interface Props {
  items: ShopItem[]
  onChange: (items: ShopItem[]) => void
  ui: UiStrings
}

export default function ShoppingList({ items, onChange, ui }: Props) {
  const hasSections = items.some(i => i.section)
  const [friscoLoading, setFriscoLoading] = useState(false)
  const [friscoResult, setFriscoResult] = useState<GroceryMatchResult | null>(null)
  const [draft, setDraft] = useState('')
  const [organizing, setOrganizing] = useState(false)
  const [copied, setCopied] = useState(false)

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

  function addItem() {
    const name = draft.trim()
    if (!name) return
    // Appended unchecked, no section — shows in the flat list until organized.
    onChange([...items, { name, checked: false }])
    setDraft('')
  }

  function removeItem(idx: number) {
    onChange(items.filter((_, i) => i !== idx))
  }

  function toggle(idx: number) {
    onChange(items.map((item, i) => i === idx ? { ...item, checked: !item.checked } : item))
  }

  function clearChecked() {
    onChange(items.filter(i => !i.checked))
  }

  // Send the current item names to the ShoppingListAgent, which merges duplicates,
  // sums/normalizes amounts and assigns a section. Result replaces the list.
  async function organize() {
    const names = items.map(i => i.name).filter(Boolean)
    if (names.length === 0) return
    setOrganizing(true)
    try {
      const resp = await fetch(`${API_BASE}/v1/shopping-list/build`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ ingredients: names }),
      })
      if (resp.ok) {
        const data = await resp.json()
        // Trust the organizer's output (it keeps every item and may rename/
        // normalize them). mergeOrganized only guards the empty-response case —
        // reconciling by name here previously duplicated renamed items into "inne".
        onChange(mergeOrganized(items, data.items ?? []))
      }
    } catch {
      // best-effort; leave the list unchanged on failure
    } finally {
      setOrganizing(false)
    }
  }

  async function copyList() {
    const text = renderListText(items, ui.shopping_list_heading ?? 'Lista zakupów')
    if (!text) return
    try {
      await navigator.clipboard.writeText(text)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch {
      // clipboard blocked — nothing else we can do silently
    }
  }

  async function shareList() {
    const text = renderListText(items, ui.shopping_list_heading ?? 'Lista zakupów')
    if (!text) return
    try {
      await navigator.share?.({ text })
    } catch {
      // user cancelled or share unsupported — no-op
    }
  }

  // navigator.share is mainly a mobile capability; only show "Udostępnij" when it exists.
  const canShare = typeof navigator !== 'undefined' && !!navigator.share

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

  const isEmpty = items.length === 0

  return (
    <div style={styles.section}>
      <h3 style={styles.sectionTitle}>{ui.shopping_list_heading ?? 'Lista zakupów'}</h3>

      <div style={styles.addRow}>
        <input
          style={styles.addInput}
          value={draft}
          onChange={e => setDraft(e.target.value)}
          onKeyDown={e => { if (e.key === 'Enter') addItem() }}
          placeholder={ui.shopping_list_add_placeholder ?? 'Dodaj pozycję…'}
        />
        <button style={styles.addBtn} onClick={addItem} disabled={!draft.trim()}>
          {ui.shopping_list_add_button ?? 'Dodaj'}
        </button>
      </div>

      <div style={styles.list}>
        {isEmpty ? (
          <div style={styles.emptyHint}>{ui.shopping_list_empty ?? 'Lista jest pusta'}</div>
        ) : hasSections ? (
          sections.map(sec => (
            <div key={sec.name}>
              <div style={styles.groupHeader}>{sec.name}</div>
              {sec.entries.map(({ item, idx }) => (
                <ItemRow key={idx} item={item} onToggle={() => toggle(idx)} onRemove={() => removeItem(idx)} />
              ))}
            </div>
          ))
        ) : (
          items.map((item, idx) => (
            <ItemRow key={idx} item={item} onToggle={() => toggle(idx)} onRemove={() => removeItem(idx)} />
          ))
        )}
      </div>

      <div style={styles.actions}>
        <button style={styles.primaryBtn} onClick={organize} disabled={organizing || isEmpty}>
          {organizing ? (ui.shopping_list_organizing ?? 'Układam…') : (ui.shopping_list_organize ?? 'Poukładaj listę zakupów')}
        </button>
      </div>
      <div style={styles.actions}>
        <button style={styles.secondaryBtn} onClick={copyList} disabled={isEmpty}>
          {copied ? (ui.shopping_list_copied ?? 'Skopiowano ✓') : (ui.shopping_list_copy ?? 'Kopiuj')}
        </button>
        {canShare && (
          <button style={styles.secondaryBtn} onClick={shareList} disabled={isEmpty}>
            {ui.shopping_list_share ?? 'Udostępnij'}
          </button>
        )}
      </div>
      <div style={styles.actions}>
        <button style={styles.clearBtn} onClick={clearChecked} disabled={isEmpty}>
          {ui.shopping_list_clear ?? 'Wyczyść zaznaczone'}
        </button>
        <button style={styles.friscoBtn} onClick={findInFrisco} disabled={friscoLoading || isEmpty}>
          {friscoLoading ? (ui.frisco_loading ?? 'Szukam w Frisco…') : (ui.frisco_button ?? 'Znajdź w Frisco')}
        </button>
      </div>

      {friscoResult && (
        <FriscoPanel result={friscoResult} ui={ui} onClose={() => setFriscoResult(null)} />
      )}
    </div>
  )
}

function ItemRow({ item, onToggle, onRemove }: { item: ShopItem; onToggle: () => void; onRemove: () => void }) {
  return (
    <div style={styles.row}>
      <label style={styles.rowLabel}>
        <input type="checkbox" checked={item.checked} onChange={onToggle} style={{ accentColor: '#c0392b' }} />
        <span style={{ ...(item.checked ? styles.strikethrough : {}) }}>{item.name}</span>
      </label>
      <button style={styles.removeBtn} onClick={onRemove} title="Usuń" aria-label="Usuń pozycję">×</button>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  section: { display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden', padding: 14, borderTop: '1px solid #e8e0d8' },
  sectionTitle: { fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: 0.5, color: '#888', marginBottom: 10 },
  addRow: { display: 'flex', gap: 6, marginBottom: 8, flexShrink: 0 },
  addInput: { flex: 1, minWidth: 0, border: '1px solid #e8e0d8', borderRadius: 6, padding: '5px 8px', fontSize: '0.84rem', outline: 'none' },
  addBtn: { background: '#fff', border: '1px solid #c0392b', borderRadius: 6, padding: '5px 12px', fontSize: '0.78rem', color: '#c0392b', cursor: 'pointer', fontWeight: 600, flexShrink: 0 },
  list: { flex: 1, overflowY: 'auto', fontSize: '0.84rem', marginBottom: 8 },
  emptyHint: { color: '#bbb', fontSize: '0.82rem', textAlign: 'center', padding: '18px 0' },
  groupHeader: { fontSize: '0.7rem', textTransform: 'uppercase', letterSpacing: 0.5, color: '#c0392b', fontWeight: 600, padding: '8px 0 3px', borderBottom: '1px solid #f0e8e0', marginBottom: 2 },
  row: { display: 'flex', alignItems: 'center', gap: 6, padding: '4px 0', borderBottom: '1px solid #f5f0eb' },
  rowLabel: { display: 'flex', alignItems: 'center', gap: 8, flex: 1, cursor: 'pointer', minWidth: 0 },
  strikethrough: { textDecoration: 'line-through', color: '#aaa' },
  removeBtn: { background: 'none', border: 'none', color: '#c99', fontSize: '1.1rem', lineHeight: 1, cursor: 'pointer', padding: '0 4px', flexShrink: 0 },
  actions: { display: 'flex', gap: 8, flexShrink: 0, alignItems: 'center', marginTop: 6 },
  primaryBtn: { background: '#c0392b', border: '1px solid #c0392b', borderRadius: 6, padding: '6px 12px', fontSize: '0.78rem', color: '#fff', cursor: 'pointer', fontWeight: 600, flex: 1 },
  secondaryBtn: { background: '#fff', border: '1px solid #c0392b', borderRadius: 6, padding: '6px 12px', fontSize: '0.78rem', color: '#c0392b', cursor: 'pointer', fontWeight: 600 },
  clearBtn: { background: 'none', border: '1px solid #e8e0d8', borderRadius: 6, padding: '4px 10px', fontSize: '0.78rem', color: '#888', cursor: 'pointer', flexShrink: 0 },
  friscoBtn: { background: 'none', border: '1px solid #e8e0d8', borderRadius: 6, padding: '4px 10px', fontSize: '0.78rem', color: '#888', cursor: 'pointer', flexShrink: 0 },
}
