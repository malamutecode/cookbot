import { GroceryMatchResult, UiStrings } from '../types'

interface Props {
  result: GroceryMatchResult
  ui: UiStrings
  onClose: () => void
}

function formatPrice(price?: number | null): string {
  return price == null ? '' : `${price.toFixed(2)} zł`
}

function formatGrammage(g?: number | null, unit?: string | null): string {
  if (g == null) return ''
  // Frisco grammage is in the product's unitOfMeasure (usually Kilogram).
  if (unit === 'Kilogram') return g < 1 ? `${Math.round(g * 1000)} g` : `${g} kg`
  return unit ? `${g} ${unit}` : `${g}`
}

export default function FriscoPanel({ result, ui, onClose }: Props) {
  return (
    <div style={styles.overlay} onClick={onClose}>
      <div style={styles.panel} onClick={e => e.stopPropagation()}>
        <div style={styles.header}>
          <h3 style={styles.heading}>{ui.frisco_heading ?? 'Produkty w Frisco'}</h3>
          <button style={styles.close} onClick={onClose} aria-label="Zamknij">×</button>
        </div>

        <div style={styles.body}>
          {result.matched.map(m => (
            <a
              key={m.ingredient + m.product.id}
              href={m.product.url}
              target="_blank"
              rel="noopener noreferrer"
              style={styles.row}
            >
              <div style={styles.thumbWrap}>
                {m.product.image_url
                  ? <img src={m.product.image_url} alt="" style={styles.thumb} />
                  : <div style={styles.thumbPlaceholder} />}
              </div>
              <div style={styles.info}>
                <div style={styles.ingredient}>{m.ingredient}</div>
                <div style={styles.productName}>{m.product.name}</div>
                <div style={styles.meta}>
                  {m.product.brand && <span>{m.product.brand}</span>}
                  {formatGrammage(m.product.grammage, m.product.unit) && (
                    <span>{formatGrammage(m.product.grammage, m.product.unit)}</span>
                  )}
                </div>
              </div>
              <div style={styles.right}>
                {formatPrice(m.product.price) && <div style={styles.price}>{formatPrice(m.product.price)}</div>}
                <span style={styles.open}>{ui.frisco_open ?? 'otwórz w Frisco'} ↗</span>
              </div>
            </a>
          ))}

          {result.unmatched.length > 0 && (
            <div style={styles.notFound}>
              <div style={styles.notFoundHeader}>
                {(ui.frisco_not_found ?? 'Nie znaleziono')} ({result.unmatched.length})
              </div>
              {result.unmatched.map(u => (
                <div key={u.ingredient} style={styles.notFoundItem}>{u.ingredient}</div>
              ))}
            </div>
          )}
        </div>

        {result.generated_at && (
          <div style={styles.footer}>
            {(ui.frisco_generated_at ?? 'Katalog z dnia')}: {result.generated_at.slice(0, 10)}
          </div>
        )}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  overlay: { position: 'fixed', inset: 0, background: 'rgba(0,0,0,0.4)', display: 'flex', alignItems: 'center', justifyContent: 'center', zIndex: 1000 },
  panel: { background: '#fff', borderRadius: 12, width: 'min(560px, 92vw)', maxHeight: '84vh', display: 'flex', flexDirection: 'column', overflow: 'hidden', boxShadow: '0 12px 40px rgba(0,0,0,0.25)' },
  header: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '14px 18px', borderBottom: '1px solid #eee' },
  heading: { fontSize: '1rem', margin: 0, color: '#c0392b' },
  close: { background: 'none', border: 'none', fontSize: '1.6rem', lineHeight: 1, cursor: 'pointer', color: '#888' },
  body: { overflowY: 'auto', padding: '8px 12px' },
  row: { display: 'flex', alignItems: 'center', gap: 12, padding: '10px 8px', borderBottom: '1px solid #f4efe9', textDecoration: 'none', color: 'inherit' },
  thumbWrap: { flexShrink: 0, width: 52, height: 52 },
  thumb: { width: 52, height: 52, objectFit: 'contain', borderRadius: 6, background: '#faf7f3' },
  thumbPlaceholder: { width: 52, height: 52, borderRadius: 6, background: '#f0e8e0' },
  info: { flex: 1, minWidth: 0 },
  ingredient: { fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: 0.4, color: '#c0392b', fontWeight: 600 },
  productName: { fontSize: '0.9rem', color: '#333', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' },
  meta: { display: 'flex', gap: 8, fontSize: '0.75rem', color: '#999' },
  right: { flexShrink: 0, textAlign: 'right', display: 'flex', flexDirection: 'column', alignItems: 'flex-end', gap: 4 },
  price: { fontSize: '0.9rem', fontWeight: 600, color: '#333' },
  open: { fontSize: '0.72rem', color: '#c0392b' },
  notFound: { padding: '12px 8px' },
  notFoundHeader: { fontSize: '0.72rem', textTransform: 'uppercase', letterSpacing: 0.4, color: '#999', fontWeight: 600, marginBottom: 6 },
  notFoundItem: { fontSize: '0.85rem', color: '#777', padding: '2px 0' },
  footer: { padding: '8px 18px', borderTop: '1px solid #eee', fontSize: '0.72rem', color: '#aaa' },
}
