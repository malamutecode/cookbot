import { useState, KeyboardEvent } from 'react'
import { SpizarniaItem, UiStrings } from '../types'

interface Props {
  items: SpizarniaItem[]
  useSpizarnia: boolean
  onToggle: (v: boolean) => void
  onAdd: (name: string) => void
  onRemove: (name: string) => void
  ui: UiStrings
}

export default function SpizarniaPanel({ items, useSpizarnia, onToggle, onAdd, onRemove, ui }: Props) {
  const [input, setInput] = useState('')

  function submit() {
    if (input.trim()) { onAdd(input.trim()); setInput('') }
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') submit()
  }

  return (
    <div style={styles.section}>
      <h3 style={styles.sectionTitle}>{ui.spizarnia_heading ?? 'Spiżarnia'}</h3>
      <label style={styles.toggle}>
        <input
          type="checkbox"
          checked={useSpizarnia}
          onChange={e => onToggle(e.target.checked)}
          style={{ accentColor: '#c0392b' }}
        />
        <span style={{ fontSize: '0.82rem' }}>{ui.spizarnia_toggle ?? 'Użyj składników z spiżarni'}</span>
      </label>
      <div style={styles.list}>
        {items.length === 0
          ? <span style={styles.empty}>{ui.spizarnia_empty ?? 'Twoja spiżarnia jest pusta'}</span>
          : items.map(item => (
              <div key={item.name} style={styles.row}>
                <span style={styles.itemName}>{item.name}</span>
                {item.quantity && <span style={styles.qty}>{item.quantity}</span>}
                <button style={styles.rmBtn} onClick={() => onRemove(item.name)} title="Usuń">✕</button>
              </div>
            ))
        }
      </div>
      <div style={styles.addRow}>
        <input
          style={styles.addInput}
          type="text"
          placeholder={ui.spizarnia_add_placeholder ?? 'Dodaj składnik…'}
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={onKey}
        />
        <button style={styles.addBtn} onClick={submit}>
          {ui.spizarnia_add_button ?? 'Dodaj'}
        </button>
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  section: { display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden', padding: 14 },
  sectionTitle: { fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: 0.5, color: '#888', marginBottom: 10 },
  toggle: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 10, cursor: 'pointer' },
  list: { flex: 1, overflowY: 'auto', fontSize: '0.84rem', marginBottom: 10 },
  empty: { color: '#888', fontStyle: 'italic', fontSize: '0.8rem' },
  row: { display: 'flex', alignItems: 'center', padding: '4px 0', borderBottom: '1px solid #f5f0eb', gap: 6 },
  itemName: { flex: 1 },
  qty: { color: '#888', fontSize: '0.76rem' },
  rmBtn: { background: 'none', border: 'none', color: '#888', fontSize: '0.9rem', cursor: 'pointer', padding: '0 2px', lineHeight: 1 },
  addRow: { display: 'flex', gap: 6, flexShrink: 0 },
  addInput: { flex: 1, border: '1px solid #e8e0d8', borderRadius: 6, padding: '5px 8px', fontSize: '0.82rem' },
  addBtn: { background: '#c0392b', color: '#fff', border: 'none', borderRadius: 6, padding: '5px 10px', fontSize: '0.8rem', cursor: 'pointer' },
}
