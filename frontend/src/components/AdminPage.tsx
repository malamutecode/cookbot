import { useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config'
import { authHeaders } from '../hooks/useSpizarnia'
import { CreatedUserView, UserUsageView } from '../types'
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

  async function deleteUser(uid: string, email?: string | null) {
    if (!confirm(`Usunąć konto ${email || uid}? Tej operacji nie można cofnąć.`)) return
    setSavingUid(uid)
    try {
      const r = await fetch(`${API_BASE}/v1/admin/users/${encodeURIComponent(uid)}`, {
        method: 'DELETE',
        headers: authHeaders(idToken),
      })
      if (r.ok) {
        await load()
      } else if (r.status === 409) {
        alert('Nie możesz usunąć własnego konta.')
      } else {
        alert(`Nie udało się usunąć konta (HTTP ${r.status}).`)
      }
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

      <CreateUserForm idToken={idToken} onCreated={load} />

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
                onDelete={deleteUser}
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

interface CreateFormProps {
  idToken: string | null
  onCreated: () => void
}

/**
 * "Dodaj użytkownika" — creates the Firebase account server-side and shows the
 * generated temp password ONCE. Nothing refetches it: the backend keeps no copy.
 */
function CreateUserForm({ idToken, onCreated }: CreateFormProps) {
  const [open, setOpen] = useState(false)
  const [email, setEmail] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [role, setRole] = useState('user')
  const [daily, setDaily] = useState('0')
  const [monthly, setMonthly] = useState('0')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [created, setCreated] = useState<CreatedUserView | null>(null)
  const [copied, setCopied] = useState(false)

  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError(null)
    try {
      const r = await fetch(`${API_BASE}/v1/admin/users`, {
        method: 'POST',
        headers: { ...authHeaders(idToken), 'content-type': 'application/json' },
        body: JSON.stringify({
          email: email.trim(),
          display_name: displayName.trim() || null,
          role,
          daily_limit: Math.max(0, Number(daily) || 0),
          monthly_limit: Math.max(0, Number(monthly) || 0),
        }),
      })
      if (r.ok) {
        setCreated(await r.json())
        setEmail(''); setDisplayName(''); setRole('user'); setDaily('0'); setMonthly('0')
        setOpen(false)
        onCreated()
        return
      }
      let detail = ''
      try { detail = (await r.json())?.detail ?? '' } catch { /* non-JSON body */ }
      setError(detail || `Nie udało się utworzyć konta (HTTP ${r.status}).`)
    } catch {
      setError('Błąd połączenia.')
    } finally {
      setBusy(false)
    }
  }

  async function copyPassword() {
    if (!created) return
    try {
      await navigator.clipboard.writeText(created.temp_password)
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    } catch { /* clipboard denied — the password is visible on screen anyway */ }
  }

  return (
    <div style={styles.createWrap}>
      {created && (
        <div style={styles.tempPanel}>
          <div style={styles.tempHeading}>Konto utworzone: {created.record.email}</div>
          <p style={styles.tempWarn}>
            To hasło tymczasowe pokazujemy tylko raz — nie zobaczysz go ponownie.
            Przekaż je użytkownikowi; przy pierwszym logowaniu ustawi własne hasło.
          </p>
          <div style={styles.tempRow}>
            <code style={styles.tempPassword}>{created.temp_password}</code>
            <button style={styles.saveBtn} onClick={copyPassword}>
              {copied ? 'Skopiowano ✓' : 'Kopiuj'}
            </button>
            <button style={styles.secondaryBtn} onClick={() => setCreated(null)}>
              Zamknij
            </button>
          </div>
        </div>
      )}

      {!open && (
        <button style={styles.saveBtn} onClick={() => { setOpen(true); setError(null) }}>
          Dodaj użytkownika
        </button>
      )}

      {open && (
        <form style={styles.createForm} onSubmit={submit}>
          <div style={styles.createGrid}>
            <label style={styles.fieldLabel}>
              E-mail
              <input
                style={styles.textInput}
                type="email"
                value={email}
                onChange={e => setEmail(e.target.value)}
                required
                disabled={busy}
              />
            </label>
            <label style={styles.fieldLabel}>
              Nazwa (opcjonalnie)
              <input
                style={styles.textInput}
                type="text"
                value={displayName}
                onChange={e => setDisplayName(e.target.value)}
                disabled={busy}
              />
            </label>
            <label style={styles.fieldLabel}>
              Rola
              <select
                style={styles.textInput}
                value={role}
                onChange={e => setRole(e.target.value)}
                disabled={busy}
              >
                <option value="user">user</option>
                <option value="admin">admin</option>
              </select>
            </label>
            <label style={styles.fieldLabel}>
              Limit dzienny (0 = ∞)
              <input
                style={styles.textInput}
                type="number"
                min={0}
                value={daily}
                onChange={e => setDaily(e.target.value)}
                disabled={busy}
              />
            </label>
            <label style={styles.fieldLabel}>
              Limit miesięczny (0 = ∞)
              <input
                style={styles.textInput}
                type="number"
                min={0}
                value={monthly}
                onChange={e => setMonthly(e.target.value)}
                disabled={busy}
              />
            </label>
          </div>
          <div style={styles.createActions}>
            <button style={styles.saveBtn} type="submit" disabled={busy}>
              {busy ? 'Tworzenie…' : 'Utwórz konto'}
            </button>
            <button style={styles.secondaryBtn} type="button" disabled={busy} onClick={() => setOpen(false)}>
              Anuluj
            </button>
          </div>
          {error && <p style={styles.createError}>{error}</p>}
        </form>
      )}
    </div>
  )
}

interface RowProps {
  row: UserUsageView
  saving: boolean
  onSaveQuota: (uid: string, daily: number, monthly: number) => void
  onToggleAdmin: (uid: string, makeAdmin: boolean) => void
  onToggleDisabled: (uid: string, disabled: boolean) => void
  onDelete: (uid: string, email?: string | null) => void
}

function UserRow({ row, saving, onSaveQuota, onToggleAdmin, onToggleDisabled, onDelete }: RowProps) {
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
        {record.display_name && <div style={styles.userName}>{record.display_name}</div>}
        <div style={styles.userUid}>{record.uid}</div>
        {record.must_change_password && (
          <div style={styles.pendingBadge}>Oczekuje na zmianę hasła</div>
        )}
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
        <button
          style={styles.disableBtn}
          disabled={saving}
          onClick={() => onDelete(record.uid, record.email)}
        >
          Usuń
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
  userName: { fontSize: '0.78rem', color: t.color.textMuted },
  userUid: { fontSize: '0.72rem', color: t.color.textFaint, fontFamily: 'monospace' },
  pendingBadge: { marginTop: 4, display: 'inline-block', fontSize: '0.7rem', fontWeight: 600, color: t.color.danger },
  createWrap: { marginBottom: 20 },
  createForm: { background: t.color.surface, border: `1px solid ${t.color.border}`, borderRadius: t.radius.lg, padding: '16px 18px', boxShadow: t.shadow.sm },
  createGrid: { display: 'flex', flexWrap: 'wrap', gap: 14 },
  createActions: { display: 'flex', gap: 8, marginTop: 14 },
  createError: { color: t.color.danger, fontSize: '0.82rem', marginTop: 10 },
  fieldLabel: { display: 'flex', flexDirection: 'column', gap: 5, fontSize: '0.75rem', fontWeight: 600, color: t.color.textMuted, minWidth: 170 },
  textInput: { border: `1px solid ${t.color.border}`, borderRadius: t.radius.md, padding: '7px 9px', fontSize: '0.85rem', outline: 'none', fontWeight: 400, color: t.color.text, background: t.color.surfaceMuted },
  tempPanel: { background: t.color.surface, border: `2px solid ${t.color.primary}`, borderRadius: t.radius.lg, padding: '16px 18px', marginBottom: 14, boxShadow: t.shadow.sm },
  tempHeading: { fontWeight: 700, color: t.color.text, marginBottom: 6 },
  tempWarn: { fontSize: '0.8rem', color: t.color.danger, marginBottom: 12, lineHeight: 1.45 },
  tempRow: { display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' },
  tempPassword: { fontFamily: 'monospace', fontSize: '1.05rem', fontWeight: 700, letterSpacing: 1, background: t.color.surfaceMuted, border: `1px solid ${t.color.border}`, borderRadius: t.radius.md, padding: '8px 12px', color: t.color.text },
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
