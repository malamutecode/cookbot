import { useState } from 'react'
import { UiStrings } from '../types'
import { API_BASE } from '../config'
import { authHeaders } from '../hooks/useSpizarnia'
import { t } from '../theme'

interface Props {
  ui: UiStrings
  idToken: string | null
  /** Called once the backend has cleared must_change_password. */
  onChanged: () => void
}

/**
 * Forced password change for admin-created accounts (STEP 44).
 *
 * Rendered by App instead of the main shell while `me.must_change_password` is
 * true. This screen is convenience only — the server enforces the same rule
 * with 423 on every product route, so hiding the shell is not the access
 * control.
 */
export default function ChangePassword({ ui, idToken, onChanged }: Props) {
  const [password, setPassword] = useState('')
  const [repeat, setRepeat] = useState('')
  const [error, setError] = useState('')
  const [saving, setSaving] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    if (password !== repeat) {
      setError(ui.password_change_mismatch ?? 'Hasła nie są takie same.')
      return
    }
    setSaving(true)
    try {
      const resp = await fetch(`${API_BASE}/v1/me/password`, {
        method: 'POST',
        headers: { ...authHeaders(idToken), 'content-type': 'application/json' },
        body: JSON.stringify({ new_password: password }),
      })
      if (resp.ok) {
        onChanged()
        return
      }
      // 422 carries a human message from validate_password (Polish copy).
      let detail = ''
      try {
        detail = (await resp.json())?.detail ?? ''
      } catch { /* non-JSON body — fall through to the generic message */ }
      setError(detail || (ui.password_change_error ?? 'Nie udało się zmienić hasła. Spróbuj ponownie.'))
    } catch {
      setError(ui.password_change_error ?? 'Nie udało się zmienić hasła. Spróbuj ponownie.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div style={styles.screen}>
      <div style={styles.box}>
        <div style={styles.logo}>
          <span style={styles.logoMark}>🔒</span>
          Feedek
        </div>
        <h2 style={styles.heading}>{ui.password_change_heading ?? 'Ustaw własne hasło'}</h2>
        <p style={styles.intro}>
          {ui.password_change_intro ??
            'Twoje konto zostało utworzone przez administratora z hasłem tymczasowym. Zanim przejdziesz dalej, ustaw własne hasło.'}
        </p>
        <form onSubmit={handleSubmit}>
          <label style={styles.label}>{ui.password_change_new ?? 'Nowe hasło'}</label>
          <input
            style={styles.input}
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            required
            minLength={8}
          />
          <label style={styles.label}>{ui.password_change_repeat ?? 'Powtórz hasło'}</label>
          <input
            style={styles.input}
            type="password"
            autoComplete="new-password"
            value={repeat}
            onChange={e => setRepeat(e.target.value)}
            required
            minLength={8}
          />
          <button style={styles.btn} type="submit" disabled={saving}>
            {saving
              ? (ui.password_change_saving ?? 'Zapisywanie…')
              : (ui.password_change_submit ?? 'Zapisz hasło')}
          </button>
        </form>
        {error && <p style={styles.error}>{error}</p>}
      </div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  screen: {
    flex: 1,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    background: `radial-gradient(1200px 600px at 50% -10%, ${t.color.primarySoft} 0%, ${t.color.bg} 55%)`,
  },
  box: {
    background: t.color.surface,
    border: `1px solid ${t.color.border}`,
    borderRadius: t.radius.xl,
    padding: '40px 36px',
    width: 380,
    boxShadow: t.shadow.lg,
  },
  logo: {
    color: t.color.primary,
    fontSize: '1.45rem',
    fontWeight: 700,
    marginBottom: 14,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  logoMark: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 38,
    height: 38,
    borderRadius: 10,
    background: t.color.primarySoft,
    fontSize: '1.15rem',
  },
  heading: { fontSize: '1.05rem', fontWeight: 600, marginBottom: 8, color: t.color.text },
  intro: { fontSize: '0.85rem', color: t.color.textMuted, marginBottom: 22, lineHeight: 1.5 },
  label: { display: 'block', fontSize: '0.82rem', fontWeight: 500, color: t.color.textMuted, marginBottom: 6 },
  input: {
    width: '100%',
    border: `1px solid ${t.color.border}`,
    borderRadius: t.radius.md,
    padding: '10px 12px',
    fontSize: '0.9rem',
    marginBottom: 16,
    background: t.color.surfaceMuted,
    boxSizing: 'border-box',
    outline: 'none',
  },
  btn: {
    width: '100%',
    background: t.color.primary,
    color: '#fff',
    border: 'none',
    borderRadius: t.radius.md,
    padding: 11,
    fontSize: '0.95rem',
    fontWeight: 600,
    cursor: 'pointer',
    marginTop: 6,
    boxShadow: t.shadow.sm,
  },
  error: { color: t.color.danger, fontSize: '0.82rem', marginTop: 12 },
}
