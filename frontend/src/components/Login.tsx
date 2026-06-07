import { useState } from 'react'
import { UiStrings } from '../types'
import { DEV_MODE } from '../config'

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
        if (email === 'admin@admin.com' && password === 'admin') {
          onLogin(null)
        } else {
          setError('Dev mode: use admin@admin.com / admin')
        }
      } else {
        // Production Firebase auth — fill in when DEV_MODE=false
        setError('Firebase auth not configured')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div style={styles.screen}>
      <div style={styles.box}>
        <div style={styles.logo}>TastyHub</div>
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
    background: '#fffdf8',
  },
  box: {
    background: '#fff',
    border: '1px solid #e8e0d8',
    borderRadius: 14,
    padding: '40px 36px',
    width: 340,
    boxShadow: '0 4px 24px rgba(0,0,0,0.09)',
  },
  logo: { color: '#c0392b', fontSize: '1.4rem', fontWeight: 'bold', marginBottom: 6 },
  heading: { fontSize: '1.1rem', marginBottom: 24, color: '#2d2d2d' },
  label: { display: 'block', fontSize: '0.82rem', color: '#888', marginBottom: 4 },
  input: {
    width: '100%',
    border: '1px solid #e8e0d8',
    borderRadius: 8,
    padding: '9px 12px',
    fontSize: '0.9rem',
    marginBottom: 14,
    background: '#fafafa',
    boxSizing: 'border-box',
  },
  btn: {
    width: '100%',
    background: '#c0392b',
    color: '#fff',
    border: 'none',
    borderRadius: 8,
    padding: 10,
    fontSize: '0.95rem',
    cursor: 'pointer',
    marginTop: 4,
  },
  error: { color: '#c0392b', fontSize: '0.82rem', marginTop: 10 },
}
