import { Page, UiStrings } from '../types'

interface Props {
  page: Page
  onNavigate: (p: Page) => void
  onLogout: () => void
  ui: UiStrings
  chatProcessing?: boolean
}

export default function NavBar({ page, onNavigate, onLogout, ui, chatProcessing }: Props) {
  return (
    <header style={styles.header}>
      <style>{`
        @keyframes nb-pulse {
          0%, 100% { opacity: 1; transform: scale(1); }
          50%       { opacity: 0.4; transform: scale(0.7); }
        }
      `}</style>
      <span style={styles.logo}>TastyHub</span>
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
      </nav>
      <button style={styles.logoutBtn} onClick={onLogout}>
        {ui.logout_button ?? 'Wyloguj'}
      </button>
    </header>
  )
}

const styles: Record<string, React.CSSProperties> = {
  header: {
    background: '#c0392b',
    color: '#fff',
    padding: '0 20px',
    display: 'flex',
    alignItems: 'center',
    gap: 16,
    height: 48,
    flexShrink: 0,
  },
  logo: { fontWeight: 'bold', fontSize: '1.1rem', letterSpacing: 0.5, marginRight: 16 },
  nav: { display: 'flex', gap: 4, flex: 1 },
  navBtn: {
    background: 'rgba(255,255,255,0.15)',
    color: '#fff',
    border: 'none',
    borderRadius: 6,
    padding: '5px 18px',
    fontSize: '0.88rem',
    cursor: 'pointer',
    transition: 'background 0.15s',
    display: 'flex',
    alignItems: 'center',
    gap: 6,
  },
  navBtnActive: {
    background: 'rgba(255,255,255,0.35)',
    fontWeight: 'bold',
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
    background: 'rgba(255,255,255,0.15)',
    color: '#fff',
    border: 'none',
    borderRadius: 6,
    padding: '5px 14px',
    fontSize: '0.82rem',
    cursor: 'pointer',
  },
}
