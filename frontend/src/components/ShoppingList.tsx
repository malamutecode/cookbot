import { useState } from 'react'
import { GroceryMatchResult, ShopItem, UiStrings } from '../types'
import { API_BASE } from '../config'
import FriscoPanel from './FriscoPanel'
import { SECTION_ORDER, mergeOrganized, renderListText } from '../lib/shoppingList'
import { t } from '../theme'

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

  // Unlike clearChecked this is unrecoverable (the whole list, checked or not),
  // so it asks first.
  function clearAll() {
    if (items.length === 0) return
    const question = ui.shopping_list_clear_all_confirm ?? 'Usunąć całą listę zakupów?'
    if (!window.confirm(question)) return
    onChange([])
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
      <div style={{ ...styles.actions, flexWrap: 'wrap' }}>
        <button style={styles.clearBtn} onClick={clearChecked} disabled={isEmpty}>
          {ui.shopping_list_clear ?? 'Wyczyść zaznaczone'}
        </button>
        <button style={styles.friscoBtn} onClick={findInFrisco} disabled={friscoLoading || isEmpty}>
          {friscoLoading ? (ui.frisco_loading ?? 'Szukam w Frisco…') : (ui.frisco_button ?? 'Znajdź w Frisco')}
        </button>
        <button style={styles.clearAllBtn} onClick={clearAll} disabled={isEmpty}>
          {ui.shopping_list_clear_all ?? 'Wyczyść wszystko'}
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
        <input type="checkbox" checked={item.checked} onChange={onToggle} style={{ accentColor: t.color.primary }} />
        <span style={{ ...(item.checked ? styles.strikethrough : {}) }}>{item.name}</span>
        {item.pantryNote && <span style={styles.pantryChip}>{item.pantryNote}</span>}
      </label>
      <button style={styles.removeBtn} onClick={onRemove} title="Usuń" aria-label="Usuń pozycję">×</button>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  section: { display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden', padding: 16, borderTop: `1px solid ${t.color.border}` },
  sectionTitle: { fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.6, color: t.color.textMuted, marginBottom: 12 },
  addRow: { display: 'flex', gap: 6, marginBottom: 10, flexShrink: 0 },
  addInput: { flex: 1, minWidth: 0, border: `1px solid ${t.color.border}`, borderRadius: t.radius.sm, padding: '7px 10px', fontSize: '0.84rem', outline: 'none' },
  addBtn: { background: t.color.surface, border: `1px solid ${t.color.primary}`, borderRadius: t.radius.sm, padding: '7px 12px', fontSize: '0.78rem', color: t.color.primary, cursor: 'pointer', fontWeight: 600, flexShrink: 0 },
  list: { flex: 1, overflowY: 'auto', fontSize: '0.84rem', marginBottom: 10 },
  emptyHint: { color: t.color.textFaint, fontSize: '0.82rem', textAlign: 'center', padding: '18px 0' },
  groupHeader: { fontSize: '0.68rem', textTransform: 'uppercase', letterSpacing: 0.6, color: t.color.primary, fontWeight: 700, padding: '10px 0 4px', borderBottom: `1px solid ${t.color.divider}`, marginBottom: 2 },
  row: { display: 'flex', alignItems: 'center', gap: 6, padding: '6px 0', borderBottom: `1px solid ${t.color.divider}` },
  rowLabel: { display: 'flex', alignItems: 'center', gap: 8, flex: 1, cursor: 'pointer', minWidth: 0, color: t.color.text },
  strikethrough: { textDecoration: 'line-through', color: t.color.textFaint },
  // Pantry tag (STEP 51) — a muted chip, deliberately quieter than the item name:
  // it is a hint to check at home, not part of what to buy.
  pantryChip: { flexShrink: 0, fontSize: '0.68rem', color: t.color.textMuted, background: t.color.bg, border: `1px solid ${t.color.border}`, borderRadius: t.radius.sm, padding: '1px 6px', whiteSpace: 'nowrap' },
  removeBtn: { background: 'none', border: 'none', color: t.color.textFaint, fontSize: '1.1rem', lineHeight: 1, cursor: 'pointer', padding: '0 4px', flexShrink: 0 },
  actions: { display: 'flex', gap: 8, flexShrink: 0, alignItems: 'center', marginTop: 8 },
  primaryBtn: { background: t.color.primary, border: `1px solid ${t.color.primary}`, borderRadius: t.radius.sm, padding: '8px 12px', fontSize: '0.78rem', color: '#fff', cursor: 'pointer', fontWeight: 600, flex: 1, boxShadow: t.shadow.sm },
  secondaryBtn: { background: t.color.surface, border: `1px solid ${t.color.primary}`, borderRadius: t.radius.sm, padding: '7px 12px', fontSize: '0.78rem', color: t.color.primary, cursor: 'pointer', fontWeight: 600 },
  clearBtn: { background: 'none', border: `1px solid ${t.color.border}`, borderRadius: t.radius.sm, padding: '6px 10px', fontSize: '0.78rem', color: t.color.textMuted, cursor: 'pointer', flexShrink: 0 },
  friscoBtn: { background: 'none', border: `1px solid ${t.color.border}`, borderRadius: t.radius.sm, padding: '6px 10px', fontSize: '0.78rem', color: t.color.textMuted, cursor: 'pointer', flexShrink: 0 },
  // Destructive: wipes the whole list, so it reads differently from "clear checked".
  clearAllBtn: { background: 'none', border: `1px solid ${t.color.danger}`, borderRadius: t.radius.sm, padding: '6px 10px', fontSize: '0.78rem', color: t.color.danger, cursor: 'pointer', flexShrink: 0 },
}
