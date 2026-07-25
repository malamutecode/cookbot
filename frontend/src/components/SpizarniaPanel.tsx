import { useState, KeyboardEvent } from 'react'
import { SpizarniaItem, UiStrings } from '../types'
import { t } from '../theme'

// Fallbacks for older servers whose /v1/ui payload predates these strings.
const INFO_FALLBACK =
  'Zapisz tu składniki, które masz w domu. Dwa niezależne ustawienia poniżej decydują, ' +
  'jak ich używam: przy proponowaniu przepisów i przy tworzeniu listy zakupów.'
const SUBTRACT_INFO_FALLBACK =
  'Gdy tworzę listę zakupów, odejmę od niej to, co masz już w spiżarni. Jeśli przy składniku ' +
  'podałaś/eś ilość, zmniejszę ją o tyle (a gdy masz wszystko — usunę pozycję). Jeśli ilości ' +
  'nie ma, zostawię pozycję w całości i tylko ją oznaczę.'

interface Props {
  items: SpizarniaItem[]
  useSpizarnia: boolean
  onToggle: (v: boolean) => void
  // Independent of useSpizarnia: that one biases which recipes get proposed,
  // this one deducts the pantry from a generated shopping list (STEP 51).
  subtractPantry: boolean
  onToggleSubtract: (v: boolean) => void
  onAdd: (name: string) => void
  onRemove: (name: string) => void
  ui: UiStrings
}

export default function SpizarniaPanel({ items, useSpizarnia, onToggle, subtractPantry, onToggleSubtract, onAdd, onRemove, ui }: Props) {
  const [input, setInput] = useState('')

  function submit() {
    if (input.trim()) { onAdd(input.trim()); setInput('') }
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') submit()
  }

  return (
    <div style={styles.section}>
      <div style={styles.titleRow}>
        <h3 style={styles.sectionTitle}>{ui.spizarnia_heading ?? 'Spiżarnia'}</h3>
        <span
          style={styles.infoIcon}
          title={ui.spizarnia_info ?? INFO_FALLBACK}
          role="img"
          aria-label={ui.spizarnia_info ?? INFO_FALLBACK}
          tabIndex={0}
        >
          ⓘ
        </span>
      </div>
      <label style={styles.toggle}>
        <input
          type="checkbox"
          checked={useSpizarnia}
          onChange={e => onToggle(e.target.checked)}
          style={{ accentColor: t.color.primary }}
        />
        <span style={{ fontSize: '0.82rem' }}>{ui.spizarnia_toggle ?? 'Użyj składników z spiżarni'}</span>
      </label>
      <label style={styles.toggle}>
        <input
          type="checkbox"
          checked={subtractPantry}
          onChange={e => onToggleSubtract(e.target.checked)}
          style={{ accentColor: t.color.primary }}
        />
        <span style={{ fontSize: '0.82rem' }}>
          {ui.spizarnia_subtract_toggle ?? 'Odejmij spiżarnię od listy zakupów'}
        </span>
        <span
          style={styles.infoIcon}
          title={ui.spizarnia_subtract_info ?? SUBTRACT_INFO_FALLBACK}
          role="img"
          aria-label={ui.spizarnia_subtract_info ?? SUBTRACT_INFO_FALLBACK}
          tabIndex={0}
        >
          ⓘ
        </span>
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
  section: { display: 'flex', flexDirection: 'column', flex: 1, overflow: 'hidden', padding: 16 },
  titleRow: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 12 },
  sectionTitle: { fontSize: '0.72rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.6, color: t.color.textMuted, margin: 0 },
  infoIcon: { fontSize: '0.82rem', color: t.color.textFaint, cursor: 'help', lineHeight: 1, userSelect: 'none' },
  toggle: { display: 'flex', alignItems: 'center', gap: 8, marginBottom: 12, cursor: 'pointer', color: t.color.text },
  list: { flex: 1, overflowY: 'auto', fontSize: '0.84rem', marginBottom: 10 },
  empty: { color: t.color.textFaint, fontStyle: 'italic', fontSize: '0.8rem' },
  row: { display: 'flex', alignItems: 'center', padding: '6px 0', borderBottom: `1px solid ${t.color.divider}`, gap: 6, color: t.color.text },
  itemName: { flex: 1 },
  qty: { color: t.color.textMuted, fontSize: '0.76rem' },
  rmBtn: { background: 'none', border: 'none', color: t.color.textFaint, fontSize: '0.9rem', cursor: 'pointer', padding: '0 2px', lineHeight: 1 },
  addRow: { display: 'flex', gap: 6, flexShrink: 0 },
  addInput: { flex: 1, border: `1px solid ${t.color.border}`, borderRadius: t.radius.sm, padding: '7px 10px', fontSize: '0.82rem', outline: 'none' },
  addBtn: { background: t.color.primary, color: '#fff', border: 'none', borderRadius: t.radius.sm, padding: '7px 12px', fontSize: '0.8rem', fontWeight: 600, cursor: 'pointer' },
}
