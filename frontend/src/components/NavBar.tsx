import { Page, UiStrings } from '../types'
import { t } from '../theme'

interface Props {
  page: Page
  onNavigate: (p: Page) => void
  onLogout: () => void
  ui: UiStrings
  chatProcessing?: boolean
  isAdmin?: boolean
}

export default function NavBar({ page, onNavigate, onLogout, ui, chatProcessing, isAdmin }: Props) {
  return (
    <header style={styles.header}>
      <style>{`
        @keyframes nb-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50%       { opacity: 0.4; transform: scale(0.7); }
        }
      `}</style>
      <span style={styles.logo}>
        <span style={styles.logoMark}>🍳</span>
        <span style={styles.brandText}>
          <span style={styles.brandName}>Feedek</span>
          <span style={styles.brandTagline}>Twój pomocnik w gotowaniu i zakupach</span>
        </span>
      </span>
      <nav style={styles.nav}>
        <button
          style={{ ...styles.navBtn, ...(page === 'chat' ? styles.navBtnActive : {}) }}
          onClick={() => onNavigate('chat')}
        >
          Chat
          {chatProcessing && (
            <span style={styles.processingDot} title="Przetwarzanie…" />
          )}
        </button>
        <button
          style={{ ...styles.navBtn, ...(page === 'sources' ? styles.navBtnActive : {}) }}
          onClick={() => onNavigate('sources')}
        >
          Źródła
        </button>
        <button
          style={{ ...styles.navBtn, ...(page === 'calendar' ? styles.navBtnActive : {}) }}
          onClick={() => onNavigate('calendar')}
        >
          Kalendarz
        </button>
        {isAdmin && (
          <button
            style={{ ...styles.navBtn, ...(page === 'admin' ? styles.navBtnActive : {}) }}
            onClick={() => onNavigate('admin')}
          >
            Admin
          </button>
        )}
      </nav>
      <button style={styles.logoutBtn} onClick={onLogout}>
        {ui.logout_button ?? 'Wyloguj'}
      </button>
    </header>
  )
}

const styles: Record<string, React.CSSProperties> = {
  header: {
    background: `linear-gradient(90deg, ${t.color.primaryHover} 0%, ${t.color.primary} 100%)`,
    color: '#fff',
    padding: '0 22px',
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    height: 56,
    flexShrink: 0,
    boxShadow: t.shadow.md,
    zIndex: 10,
  },
  logo: {
    marginRight: 12,
    display: 'flex',
    alignItems: 'center',
    gap: 10,
  },
  brandText: {
    display: 'flex',
    flexDirection: 'column',
    lineHeight: 1.15,
  },
  brandName: {
    fontWeight: 700,
    fontSize: '1.15rem',
    letterSpacing: 0.2,
  },
  brandTagline: {
    fontSize: '0.68rem',
    fontWeight: 400,
    color: 'rgba(255,255,255,0.78)',
    letterSpacing: 0.1,
  },
  logoMark: {
    display: 'inline-flex',
    alignItems: 'center',
    justifyContent: 'center',
    width: 30,
    height: 30,
    borderRadius: 8,
    background: 'rgba(255,255,255,0.18)',
    fontSize: '1rem',
  },
  nav: { display: 'flex', gap: 6, flex: 1 },
  navBtn: {
    background: 'transparent',
    color: 'rgba(255,255,255,0.82)',
    border: 'none',
    borderRadius: t.radius.md,
    padding: '7px 16px',
    fontSize: '0.88rem',
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'background 0.15s, color 0.15s',
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  navBtnActive: {
    background: 'rgba(255,255,255,0.22)',
    color: '#fff',
    fontWeight: 600,
  },
  processingDot: {
    display: 'inline-block',
    width: 7,
    height: 7,
    borderRadius: '50%',
    background: '#fff',
    animation: 'nb-pulse 1s ease-in-out infinite',
    flexShrink: 0,
  },
  logoutBtn: {
    background: 'rgba(255,255,255,0.14)',
    color: '#fff',
    border: '1px solid rgba(255,255,255,0.25)',
    borderRadius: t.radius.md,
    padding: '6px 15px',
    fontSize: '0.82rem',
    fontWeight: 500,
    cursor: 'pointer',
    transition: 'background 0.15s',
  },
}
