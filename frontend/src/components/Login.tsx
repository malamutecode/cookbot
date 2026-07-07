import { useState } from 'react'
import { UiStrings } from '../types'
import { DEV_MODE } from '../config'
import { t } from '../theme'

interface Props {
  ui: UiStrings
  onLogin: (idToken: string | null) => void
}

export default function Login({ ui, onLogin }: Props) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    try {
      if (DEV_MODE) {
        // Local dev bypass — no real auth; App uses the dev-uid identity.
        if (email === 'admin@admin.com' && password === 'admin') {
          onLogin(null)
        } else {
          setError('Dev mode: use admin@admin.com / admin')
        }
        return
      }
      // Production: real Firebase email/password auth → ID token.
      const { signInWithEmailAndPassword } = await import('firebase/auth')
      const { firebaseAuth } = await import('../firebase')
      const cred = await signInWithEmailAndPassword(firebaseAuth(), email.trim(), password)
      const idToken = await cred.user.getIdToken()
      onLogin(idToken)
    } catch (err) {
      const code = (err as { code?: string })?.code ?? ''
      if (code === 'auth/invalid-credential' || code === 'auth/wrong-password' || code === 'auth/user-not-found') {
        setError('Nieprawidłowy e-mail lub hasło.')
      } else if (code === 'auth/too-many-requests') {
        setError('Zbyt wiele prób. Spróbuj ponownie później.')
      } else {
        setError('Logowanie nie powiodło się. Spróbuj ponownie.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={styles.screen}>
      <div style={styles.box}>
        <div style={styles.logo}>
          <span style={styles.logoMark}>🍳</span>
          Feedek
        </div>
        <div style={styles.tagline}>Twój pomocnik w gotowaniu i zakupach</div>
        <h2 style={styles.heading}>{ui.login_heading ?? 'Zaloguj się'}</h2>
        <form onSubmit={handleSubmit}>
          <label style={styles.label}>{ui.login_email ?? 'E-mail'}</label>
          <input
            style={styles.input}
            type="email"
            autoComplete="email"
            value={email}
            onChange={e => setEmail(e.target.value)}
            required
          />
          <label style={styles.label}>{ui.login_password ?? 'Hasło'}</label>
          <input
            style={styles.input}
            type="password"
            autoComplete="current-password"
            value={password}
            onChange={e => setPassword(e.target.value)}
            required
          />
          <button style={styles.btn} type="submit" disabled={loading}>
            {loading ? '…' : (ui.login_button ?? 'Zaloguj się')}
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
    width: 360,
    boxShadow: t.shadow.lg,
  },
  logo: {
    color: t.color.primary,
    fontSize: '1.45rem',
    fontWeight: 700,
    marginBottom: 4,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  tagline: {
    fontSize: '0.82rem',
    color: t.color.textMuted,
    marginBottom: 18,
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
  heading: { fontSize: '1.05rem', fontWeight: 600, marginBottom: 26, color: t.color.text },
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
