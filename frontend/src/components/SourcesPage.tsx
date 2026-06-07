import { useState, useEffect } from 'react'
import { API_BASE } from '../config'
import { authHeaders } from '../hooks/useSpizarnia'

interface RecipeSource {
  url: string
  name: string
  enabled: boolean
}

interface SearchPrefs {
  uid: string
  sources: RecipeSource[]
  search_mode: string
  allow_ai_generated: boolean
}

interface Props {
  idToken: string | null
}

const SEARCH_MODE_LABELS: Record<string, string> = {
  sites_only: 'Tylko zapisane strony',
  sites_and_internet: 'Zapisane strony + internet',
  internet_only: 'Cały internet',
}

export default function SourcesPage({ idToken }: Props) {
  const [prefs, setPrefs] = useState<SearchPrefs | null>(null)
  const [loading, setLoading] = useState(true)
  const [newUrl, setNewUrl] = useState('')
  const [newName, setNewName] = useState('')
  const [saving, setSaving] = useState(false)

  async function load() {
    setLoading(true)
    try {
      const r = await fetch(`${API_BASE}/v1/search-prefs`, { headers: authHeaders(idToken) })
      if (r.ok) setPrefs(await r.json())
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => { load() }, [idToken])

  async function patchPrefs(patch: Partial<SearchPrefs>) {
    if (!prefs) return
    setSaving(true)
    const r = await fetch(`${API_BASE}/v1/search-prefs`, {
      method: 'PUT',
      headers: { ...authHeaders(idToken), 'content-type': 'application/json' },
      body: JSON.stringify(patch),
    })
    if (r.ok) setPrefs(await r.json())
    setSaving(false)
  }

  async function setMode(mode: string) {
    await patchPrefs({ search_mode: mode })
  }

  async function toggleAiGenerated(val: boolean) {
    await patchPrefs({ allow_ai_generated: val })
  }

  async function toggleSource(url: string, enabled: boolean) {
    if (!prefs) return
    const updated = prefs.sources.map(s => s.url === url ? { ...s, enabled } : s)
    setSaving(true)
    const r = await fetch(`${API_BASE}/v1/search-prefs`, {
      method: 'PUT',
      headers: { ...authHeaders(idToken), 'content-type': 'application/json' },
      body: JSON.stringify({ sources: updated }),
    })
    if (r.ok) setPrefs(await r.json())
    setSaving(false)
  }

  async function addSource() {
    const url = newUrl.trim().replace(/^https?:\/\//, '').replace(/\/$/, '')
    const name = newName.trim() || url
    if (!url) return
    setSaving(true)
    const r = await fetch(`${API_BASE}/v1/search-prefs/sources`, {
      method: 'POST',
      headers: { ...authHeaders(idToken), 'content-type': 'application/json' },
      body: JSON.stringify({ url, name }),
    })
    if (r.ok) { setPrefs(await r.json()); setNewUrl(''); setNewName('') }
    setSaving(false)
  }

  async function removeSource(url: string) {
    setSaving(true)
    const r = await fetch(`${API_BASE}/v1/search-prefs/sources/${encodeURIComponent(url)}`, {
      method: 'DELETE',
      headers: authHeaders(idToken),
    })
    if (r.ok) setPrefs(await r.json())
    setSaving(false)
  }

  if (loading) return <div style={styles.page}><p style={{ color: '#888' }}>Ładowanie…</p></div>
  if (!prefs) return <div style={styles.page}><p style={{ color: '#c0392b' }}>Błąd ładowania ustawień.</p></div>

  return (
    <div style={styles.page}>
      <h2 style={styles.heading}>Źródła przepisów</h2>

      {/* AI generation toggle */}
      <section style={styles.section}>
        <h3 style={styles.sectionTitle}>Generowanie przez AI</h3>
        <label style={styles.toggleRow}>
          <span style={styles.toggleLabel}>
            <span style={{ fontWeight: 500, fontSize: '0.9rem' }}>Przepisy generowane przez AI</span>
            <span style={styles.toggleDesc}>
              {prefs.allow_ai_generated
                ? 'Gdy strony nie zawierają odpowiedniego przepisu, AI tworzy własny.'
                : 'Tylko przepisy znalezione na stronach — bez generowania przez AI.'}
            </span>
          </span>
          <button
            role="switch"
            aria-checked={prefs.allow_ai_generated}
            style={{ ...styles.toggle, ...(prefs.allow_ai_generated ? styles.toggleOn : styles.toggleOff) }}
            onClick={() => toggleAiGenerated(!prefs.allow_ai_generated)}
            disabled={saving}
          >
            <span style={{ ...styles.toggleThumb, ...(prefs.allow_ai_generated ? styles.toggleThumbOn : {}) }} />
          </button>
        </label>
      </section>

      {/* Search mode */}
      <section style={styles.section}>
        <h3 style={styles.sectionTitle}>Tryb wyszukiwania</h3>
        <div style={styles.modeGroup}>
          {Object.entries(SEARCH_MODE_LABELS).map(([mode, label]) => (
            <label key={mode} style={styles.modeLabel}>
              <input
                type="radio"
                name="search_mode"
                value={mode}
                checked={prefs.search_mode === mode}
                onChange={() => setMode(mode)}
                disabled={saving}
                style={{ accentColor: '#c0392b' }}
              />
              {label}
            </label>
          ))}
        </div>
      </section>

      {/* Saved sites */}
      <section style={styles.section}>
        <h3 style={styles.sectionTitle}>Zapisane strony</h3>
        {prefs.sources.length === 0 && <p style={styles.empty}>Brak zapisanych stron.</p>}
        <div style={styles.sourceList}>
          {prefs.sources.map(src => (
            <div key={src.url} style={styles.sourceRow}>
              <label style={styles.sourceLabel}>
                <input
                  type="checkbox"
                  checked={src.enabled}
                  onChange={e => toggleSource(src.url, e.target.checked)}
                  disabled={saving}
                  style={{ accentColor: '#c0392b' }}
                />
                <div>
                  <div style={styles.sourceName}>{src.name}</div>
                  <div style={styles.sourceUrl}>{src.url}</div>
                </div>
              </label>
              <button style={styles.removeBtn} onClick={() => removeSource(src.url)} disabled={saving} title="Usuń">✕</button>
            </div>
          ))}
        </div>

        {/* Add new site */}
        <div style={styles.addRow}>
          <input
            style={styles.addInput}
            type="text"
            placeholder="URL strony (np. mojastrona.pl)"
            value={newUrl}
            onChange={e => setNewUrl(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addSource()}
          />
          <input
            style={{ ...styles.addInput, maxWidth: 160 }}
            type="text"
            placeholder="Nazwa (opcjonalnie)"
            value={newName}
            onChange={e => setNewName(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && addSource()}
          />
          <button style={styles.addBtn} onClick={addSource} disabled={saving || !newUrl.trim()}>
            Dodaj
          </button>
        </div>
      </section>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  page: { flex: 1, overflowY: 'auto', padding: '24px 32px', background: '#fffdf8', maxWidth: 640 },
  heading: { fontSize: '1.1rem', fontWeight: 700, marginBottom: 20, color: '#2d2d2d' },

  section: { marginBottom: 28 },
  sectionTitle: { fontSize: '0.75rem', textTransform: 'uppercase', letterSpacing: 0.5, color: '#888', marginBottom: 12 },

  modeGroup: { display: 'flex', flexDirection: 'column', gap: 8 },
  modeLabel: { display: 'flex', alignItems: 'center', gap: 8, fontSize: '0.9rem', cursor: 'pointer' },

  empty: { color: '#aaa', fontSize: '0.85rem' },
  sourceList: { display: 'flex', flexDirection: 'column', gap: 6, marginBottom: 12 },
  sourceRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', background: '#fff', border: '1px solid #e8e0d8', borderRadius: 8, padding: '8px 12px' },
  sourceLabel: { display: 'flex', alignItems: 'center', gap: 10, cursor: 'pointer', flex: 1 },
  sourceName: { fontSize: '0.88rem', fontWeight: 500 },
  sourceUrl: { fontSize: '0.75rem', color: '#888' },
  removeBtn: { background: 'none', border: 'none', color: '#bbb', cursor: 'pointer', fontSize: '0.8rem', padding: '0 4px' },

  toggleRow: { display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: 16, cursor: 'pointer' },
  toggleLabel: { display: 'flex', flexDirection: 'column', gap: 3 },
  toggleDesc: { fontSize: '0.78rem', color: '#888' },
  toggle: { flexShrink: 0, width: 44, height: 24, borderRadius: 12, border: 'none', cursor: 'pointer', position: 'relative', transition: 'background 0.2s', padding: 0 },
  toggleOn: { background: '#27ae60' },
  toggleOff: { background: '#ccc' },
  toggleThumb: { position: 'absolute', top: 3, left: 3, width: 18, height: 18, borderRadius: '50%', background: '#fff', transition: 'left 0.2s', display: 'block' },
  toggleThumbOn: { left: 23 },

  addRow: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  addInput: { flex: 1, minWidth: 160, border: '1px solid #e8e0d8', borderRadius: 6, padding: '7px 10px', fontSize: '0.85rem' },
  addBtn: { background: '#c0392b', color: '#fff', border: 'none', borderRadius: 6, padding: '7px 16px', fontSize: '0.85rem', cursor: 'pointer', whiteSpace: 'nowrap' },
}
