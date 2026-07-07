import { useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config'
import { authHeaders } from '../hooks/useSpizarnia'
import { UserUsageView } from '../types'
import { t } from '../theme'

interface Props {
  idToken: string | null
}

function fmtLimit(n: number): string {
  return n > 0 ? n.toLocaleString('pl-PL') : '∞'
}

export default function AdminPage({ idToken }: Props) {
  const [rows, setRows] = useState<UserUsageView[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [savingUid, setSavingUid] = useState<string | null>(null)

  const load = useCallback(async () => {
    setLoading(true)
    setError(null)
    try {
      const r = await fetch(`${API_BASE}/v1/admin/users`, { headers: authHeaders(idToken) })
      if (r.status === 403) { setError('Brak uprawnień administratora.'); setRows([]); return }
      if (!r.ok) { setError(`Błąd ładowania (HTTP ${r.status}).`); return }
      setRows(await r.json())
    } catch {
      setError('Błąd połączenia.')
    } finally {
      setLoading(false)
    }
  }, [idToken])

  useEffect(() => { load() }, [load])

  async function saveQuota(uid: string, daily_limit: number, monthly_limit: number) {
    setSavingUid(uid)
    try {
      const r = await fetch(`${API_BASE}/v1/admin/users/${encodeURIComponent(uid)}/quota`, {
        method: 'PUT',
        headers: { ...authHeaders(idToken), 'content-type': 'application/json' },
        body: JSON.stringify({ daily_limit, monthly_limit }),
      })
      if (r.ok) await load()
    } finally {
      setSavingUid(null)
    }
  }

  async function toggleAdmin(uid: string, makeAdmin: boolean) {
    setSavingUid(uid)
    try {
      const r = await fetch(`${API_BASE}/v1/admin/users/${encodeURIComponent(uid)}/role`, {
        method: 'PUT',
        headers: { ...authHeaders(idToken), 'content-type': 'application/json' },
        body: JSON.stringify({ role: makeAdmin ? 'admin' : 'user' }),
      })
      if (r.ok) await load()
    } finally {
      setSavingUid(null)
    }
  }

  async function toggleDisabled(uid: string, disabled: boolean) {
    setSavingUid(uid)
    try {
      const r = await fetch(`${API_BASE}/v1/admin/users/${encodeURIComponent(uid)}/disabled`, {
        method: 'PUT',
        headers: { ...authHeaders(idToken), 'content-type': 'application/json' },
        body: JSON.stringify({ disabled }),
      })
      if (r.ok) await load()
    } finally {
      setSavingUid(null)
    }
  }

  if (loading) return <div style={styles.page}><p style={{ color: '#888' }}>Ładowanie…</p></div>
  if (error) return <div style={styles.page}><p style={{ color: t.color.danger }}>{error}</p></div>

  return (
    <div style={styles.page}>
      <h2 style={styles.heading}>Zarządzanie użytkownikami</h2>
      <p style={styles.hint}>
        Limit 0 oznacza brak ograniczenia (∞). Zużycie liczone jest w tokenach na dzień / miesiąc.
      </p>

      <div style={styles.tableWrap}>
        <table style={styles.table}>
          <thead>
            <tr>
              <th style={styles.th}>Użytkownik</th>
              <th style={styles.th}>Rola</th>
              <th style={styles.thNum}>Limit dzienny</th>
              <th style={styles.thNum}>Zużyto dziś</th>
              <th style={styles.thNum}>Limit miesięczny</th>
              <th style={styles.thNum}>Zużyto w miesiącu</th>
              <th style={styles.th}>Status</th>
              <th style={styles.th}></th>
            </tr>
          </thead>
          <tbody>
            {rows.map(row => (
              <UserRow
                key={row.record.uid}
                row={row}
                saving={savingUid === row.record.uid}
                onSaveQuota={saveQuota}
                onToggleAdmin={toggleAdmin}
                onToggleDisabled={toggleDisabled}
              />
            ))}
            {rows.length === 0 && (
              <tr><td colSpan={8} style={styles.empty}>Brak użytkowników.</td></tr>
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}

interface RowProps {
  row: UserUsageView
  saving: boolean
  onSaveQuota: (uid: string, daily: number, monthly: number) => void
  onToggleAdmin: (uid: string, makeAdmin: boolean) => void
  onToggleDisabled: (uid: string, disabled: boolean) => void
}

function UserRow({ row, saving, onSaveQuota, onToggleAdmin, onToggleDisabled }: RowProps) {
  const { record, daily_used, monthly_used } = row
  const [daily, setDaily] = useState(String(record.quota.daily_limit))
  const [monthly, setMonthly] = useState(String(record.quota.monthly_limit))

  // Re-sync editable fields when the underlying record changes (after a save/reload).
  useEffect(() => { setDaily(String(record.quota.daily_limit)) }, [record.quota.daily_limit])
  useEffect(() => { setMonthly(String(record.quota.monthly_limit)) }, [record.quota.monthly_limit])

  const dirty =
    Number(daily) !== record.quota.daily_limit ||
    Number(monthly) !== record.quota.monthly_limit

  return (
    <tr style={record.disabled ? styles.rowDisabled : undefined}>
      <td style={styles.td}>
        <div style={styles.userEmail}>{record.email || '—'}</div>
        <div style={styles.userUid}>{record.uid}</div>
      </td>
      <td style={styles.td}>
        <span style={record.role === 'admin' ? styles.badgeAdmin : styles.badgeUser}>{record.role}</span>
      </td>
      <td style={styles.tdNum}>
        <input
          style={styles.numInput}
          type="number"
          min={0}
          value={daily}
          onChange={e => setDaily(e.target.value)}
          disabled={saving}
        />
      </td>
      <td style={styles.tdNum}>{daily_used.toLocaleString('pl-PL')} / {fmtLimit(record.quota.daily_limit)}</td>
      <td style={styles.tdNum}>
        <input
          style={styles.numInput}
          type="number"
          min={0}
          value={monthly}
          onChange={e => setMonthly(e.target.value)}
          disabled={saving}
        />
      </td>
      <td style={styles.tdNum}>{monthly_used.toLocaleString('pl-PL')} / {fmtLimit(record.quota.monthly_limit)}</td>
      <td style={styles.td}>
        <span style={record.disabled ? styles.badgeDisabled : styles.badgeActive}>
          {record.disabled ? 'Wyłączony' : 'Aktywny'}
        </span>
      </td>
      <td style={styles.tdActions}>
        {dirty && (
          <button
            style={styles.saveBtn}
            disabled={saving}
            onClick={() => onSaveQuota(record.uid, Math.max(0, Number(daily) || 0), Math.max(0, Number(monthly) || 0))}
          >
            Zapisz
          </button>
        )}
        <button
          style={styles.secondaryBtn}
          disabled={saving}
          onClick={() => onToggleAdmin(record.uid, record.role !== 'admin')}
        >
          {record.role === 'admin' ? 'Odbierz admina' : 'Nadaj admina'}
        </button>
        <button
          style={record.disabled ? styles.enableBtn : styles.disableBtn}
          disabled={saving}
          onClick={() => onToggleDisabled(record.uid, !record.disabled)}
        >
          {record.disabled ? 'Włącz' : 'Wyłącz'}
        </button>
      </td>
    </tr>
  )
}

const styles: Record<string, React.CSSProperties> = {
  page: { flex: 1, overflowY: 'auto', padding: '28px 32px', background: t.color.bg },
  heading: { fontSize: '1.25rem', fontWeight: 700, marginBottom: 6, color: t.color.text },
  hint: { fontSize: '0.82rem', color: t.color.textMuted, marginBottom: 20 },
  tableWrap: { overflowX: 'auto', background: t.color.surface, border: `1px solid ${t.color.border}`, borderRadius: t.radius.lg, boxShadow: t.shadow.sm },
  table: { width: '100%', borderCollapse: 'collapse', fontSize: '0.85rem' },
  th: { textAlign: 'left', padding: '12px 14px', fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: t.color.textMuted, borderBottom: `1px solid ${t.color.border}`, whiteSpace: 'nowrap' },
  thNum: { textAlign: 'right', padding: '12px 14px', fontSize: '0.7rem', fontWeight: 700, textTransform: 'uppercase', letterSpacing: 0.5, color: t.color.textMuted, borderBottom: `1px solid ${t.color.border}`, whiteSpace: 'nowrap' },
  td: { padding: '10px 14px', borderBottom: `1px solid ${t.color.border}`, color: t.color.text, verticalAlign: 'middle' },
  tdNum: { padding: '10px 14px', borderBottom: `1px solid ${t.color.border}`, color: t.color.text, textAlign: 'right', whiteSpace: 'nowrap', verticalAlign: 'middle' },
  tdActions: { padding: '10px 14px', borderBottom: `1px solid ${t.color.border}`, display: 'flex', gap: 6, flexWrap: 'wrap', justifyContent: 'flex-end' },
  rowDisabled: { opacity: 0.55 },
  userEmail: { fontWeight: 500, color: t.color.text },
  userUid: { fontSize: '0.72rem', color: t.color.textFaint, fontFamily: 'monospace' },
  numInput: { width: 92, border: `1px solid ${t.color.border}`, borderRadius: t.radius.md, padding: '6px 8px', fontSize: '0.82rem', outline: 'none', textAlign: 'right' },
  badgeAdmin: { background: t.color.primary, color: '#fff', padding: '2px 9px', borderRadius: t.radius.pill, fontSize: '0.72rem', fontWeight: 600 },
  badgeUser: { background: t.color.surfaceMuted, color: t.color.textMuted, padding: '2px 9px', borderRadius: t.radius.pill, fontSize: '0.72rem', fontWeight: 600 },
  badgeActive: { color: t.color.text, fontSize: '0.8rem' },
  badgeDisabled: { color: t.color.danger, fontSize: '0.8rem', fontWeight: 600 },
  empty: { padding: '18px', textAlign: 'center', color: t.color.textFaint },
  saveBtn: { background: t.color.primary, color: '#fff', border: 'none', borderRadius: t.radius.md, padding: '6px 12px', fontSize: '0.78rem', fontWeight: 600, cursor: 'pointer' },
  secondaryBtn: { background: t.color.surfaceMuted, color: t.color.text, border: `1px solid ${t.color.border}`, borderRadius: t.radius.md, padding: '6px 12px', fontSize: '0.78rem', cursor: 'pointer' },
  disableBtn: { background: 'none', color: t.color.danger, border: `1px solid ${t.color.danger}`, borderRadius: t.radius.md, padding: '6px 12px', fontSize: '0.78rem', cursor: 'pointer' },
  enableBtn: { background: 'none', color: t.color.text, border: `1px solid ${t.color.border}`, borderRadius: t.radius.md, padding: '6px 12px', fontSize: '0.78rem', cursor: 'pointer' },
}
