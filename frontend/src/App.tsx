import { useState, useEffect, useCallback } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import Login from './components/Login'
import NavBar from './components/NavBar'
import SpizarniaPanel from './components/SpizarniaPanel'
import ShoppingList from './components/ShoppingList'
import ChatPanel from './components/ChatPanel'
import CalendarPage from './components/CalendarPage'
import SourcesPage from './components/SourcesPage'
import AdminPage from './components/AdminPage'
import ChangePassword from './components/ChangePassword'
import { useSpizarnia, authHeaders } from './hooks/useSpizarnia'
import { useCalendar } from './hooks/useCalendar'
import { Page, UiStrings, ShopItem, CalendarDay, CalendarEntry, MeView } from './types'
import { API_BASE, TEST_USER, DEV_MODE } from './config'
import { t } from './theme'

// The calendar is server-side (STEP 52) — no CAL_KEY here. The shopping list is
// still local: it is a scratchpad, not a plan that has to follow the user.
const SHOP_KEY = 'tastyhub_shopping'

function loadShopItems(): ShopItem[] {
  try { return JSON.parse(localStorage.getItem(SHOP_KEY) ?? '[]') } catch { return [] }
}
function saveShopItems(items: ShopItem[]) {
  localStorage.setItem(SHOP_KEY, JSON.stringify(items))
}

export default function App() {
  const [loggedIn, setLoggedIn]   = useState(false)
  const [idToken, setIdToken]     = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string>('')
  const [page, setPage]           = useState<Page>('chat')
  const [ui, setUi]               = useState<UiStrings>({})
  const [spizEnabled, setSpizEnabled] = useState(false)
  // Independent of spizEnabled — deducts the pantry from a generated shopping
  // list (STEP 51). Sent per-turn to the chat and per-request to the calendar's
  // list builder, so toggling it takes effect without reconnecting.
  const [spizSubtract, setSpizSubtract] = useState(false)
  const [shopItems, setShopItems] = useState<ShopItem[]>(loadShopItems)
  const [chatProcessing, setChatProcessing] = useState(false)
  const [isAdmin, setIsAdmin]     = useState(false)
  // Admin-created accounts start with a temp password and must replace it
  // before the shell is usable (STEP 44). The server enforces the same rule.
  const [mustChangePassword, setMustChangePassword] = useState(false)

  const { items: spizItems, load: loadSpiz, add: addSpiz, remove: removeSpiz } = useSpizarnia(idToken)
  const {
    days: calDays,
    load: loadCal,
    save: saveCal,
    addEntry: addCalEntry,
    removeEntry: removeCalEntry,
  } = useCalendar(idToken)

  useEffect(() => {
    fetch(`${API_BASE}/v1/ui-strings`)
      .then(r => r.json())
      .then(setUi)
      .catch(() => {})
  }, [])

  // Persist the shopping list to localStorage on every change (like the calendar),
  // so a hand-built list survives reloads.
  useEffect(() => { saveShopItems(shopItems) }, [shopItems])

  const createSession = useCallback(async (token: string | null) => {
    const resp = await fetch(`${API_BASE}/v1/sessions`, { method: 'POST', headers: authHeaders(token) })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    setSessionId(data.session_id)
  }, [])

  async function handleLogin(token: string | null) {
    setIdToken(token)
    // /v1/me FIRST: a temp-password account is refused (423) by /v1/sessions and
    // every other product route, so we have to know about the lock before we
    // try to bootstrap the shell.
    let locked = false
    try {
      const me: MeView = await fetch(`${API_BASE}/v1/me`, { headers: authHeaders(token) }).then(r => r.json())
      setIsAdmin(!!me.is_admin)
      locked = !!me.must_change_password
      setMustChangePassword(locked)
    } catch { setIsAdmin(false) }

    if (!locked) {
      await createSession(token)
      await loadSpiz()
      await loadCal()
    }
    setLoggedIn(true)
  }

  // Called by ChangePassword once the backend has cleared the flag — finish the
  // bootstrap that handleLogin skipped while the account was locked.
  const handlePasswordChanged = useCallback(async () => {
    setMustChangePassword(false)
    try {
      await createSession(idToken)
      await loadSpiz()
      await loadCal()
    } catch { /* the user can reload; the account is no longer locked */ }
  }, [idToken, createSession, loadSpiz, loadCal])

  // Dev convenience: when VITE_TEST_USER=true, auto-login as the dev user so the
  // login screen is skipped during local smoke-testing. Runs once on mount.
  useEffect(() => {
    if (TEST_USER && !loggedIn) {
      handleLogin(null).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  // Keep the ID token fresh. Firebase auto-refreshes (~hourly); onIdTokenChanged
  // fires with the new token so long chat sessions and WS reconnects stay valid.
  // Dev mode uses the dev-uid bypass and has no Firebase session to watch.
  useEffect(() => {
    if (DEV_MODE || !loggedIn) return
    let unsub = () => {}
    ;(async () => {
      const { onIdTokenChanged } = await import('firebase/auth')
      const { firebaseAuth } = await import('./firebase')
      unsub = onIdTokenChanged(firebaseAuth(), async user => {
        setIdToken(user ? await user.getIdToken() : null)
      })
    })().catch(() => {})
    return () => unsub()
  }, [loggedIn])

  async function handleLogout() {
    if (!DEV_MODE) {
      try {
        const { signOut } = await import('firebase/auth')
        const { firebaseAuth } = await import('./firebase')
        await signOut(firebaseAuth())
      } catch { /* ignore — clearing local state below is enough */ }
    }
    setLoggedIn(false)
    setIdToken(null)
    setSessionId('')
    setIsAdmin(false)
    setMustChangePassword(false)
    setPage('chat')
  }

  function handleCalendarChange(days: CalendarDay[]) {
    void saveCal(days)
  }

  // The server has already persisted an agent-driven add by the time the WS
  // message arrives (STEP 52) — the hook's PUT here reconciles the day buckets
  // the grid renders, and is what makes the entry appear without a reload.
  function handleAddToCalendar(entry: CalendarEntry) {
    addCalEntry(entry)
    setPage('calendar')
  }

  function handleExportToShoppingList(newItems: ShopItem[]) {
    const merged = [...shopItems]
    for (const item of newItems) {
      if (!merged.some(i => i.name.toLowerCase() === item.name.toLowerCase())) {
        merged.push(item)
      }
    }
    setShopItems(merged)
    setPage('chat')
  }

  if (!loggedIn) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
        <Login ui={ui} onLogin={handleLogin} />
      </div>
    )
  }

  // Forced password change replaces the whole shell — an admin-created account
  // cannot reach any product route until the temp password is replaced.
  if (mustChangePassword) {
    return (
      <div style={{ display: 'flex', flexDirection: 'column', height: '100vh' }}>
        <ChangePassword ui={ui} idToken={idToken} onChanged={handlePasswordChanged} />
      </div>
    )
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', fontFamily: t.font.sans, background: t.color.bg }}>
      <NavBar page={page} onNavigate={setPage} onLogout={handleLogout} ui={ui} chatProcessing={chatProcessing} isAdmin={isAdmin} />

      {/* Chat page — always mounted to preserve WebSocket + message history */}
      <div style={{ flex: 1, overflow: 'hidden', display: page === 'chat' ? 'flex' : 'none' }}>
        {sessionId && (
          <PanelGroup direction="horizontal" style={{ flex: 1, overflow: 'hidden' }}>
            <Panel defaultSize={28} minSize={18} maxSize={50} style={{ display: 'flex', flexDirection: 'column', background: t.color.surface, borderRight: `1px solid ${t.color.border}`, overflow: 'hidden' }}>
              <PanelGroup direction="vertical">
                <Panel defaultSize={20} minSize={12} style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                  <SpizarniaPanel
                    items={spizItems}
                    useSpizarnia={spizEnabled}
                    onToggle={setSpizEnabled}
                    subtractPantry={spizSubtract}
                    onToggleSubtract={setSpizSubtract}
                    onAdd={addSpiz}
                    onRemove={removeSpiz}
                    ui={ui}
                  />
                </Panel>
                <PanelResizeHandle style={{ height: 4, background: t.color.border, cursor: 'row-resize' }} />
                <Panel defaultSize={80} minSize={20} style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                  <ShoppingList items={shopItems} onChange={setShopItems} ui={ui} />
                </Panel>
              </PanelGroup>
            </Panel>

            <PanelResizeHandle style={{ width: 5, background: t.color.border, cursor: 'col-resize' }} />

            <Panel minSize={40} style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <ChatPanel
                sessionId={sessionId}
                idToken={idToken}
                useSpizarnia={spizEnabled}
                subtractPantry={spizSubtract}
                ui={ui}
                shopItems={shopItems}
                onShopItemsChange={setShopItems}
                onAddToCalendar={handleAddToCalendar}
                onCalendarRemove={removeCalEntry}
                onProcessingChange={setChatProcessing}
              />
            </Panel>
          </PanelGroup>
        )}
      </div>

      {/* Sources page */}
      <div style={{ flex: 1, overflow: 'hidden', display: page === 'sources' ? 'flex' : 'none', flexDirection: 'column' }}>
        <SourcesPage idToken={idToken} />
      </div>

      {/* Calendar page */}
      <div style={{ flex: 1, overflow: 'hidden', display: page === 'calendar' ? 'flex' : 'none', flexDirection: 'column' }}>
        <CalendarPage
          days={calDays}
          onChange={handleCalendarChange}
          onExportToShoppingList={handleExportToShoppingList}
          ui={ui}
          subtractPantry={spizSubtract}
          idToken={idToken}
        />
      </div>

      {/* Admin page — only rendered for admins */}
      {isAdmin && (
        <div style={{ flex: 1, overflow: 'hidden', display: page === 'admin' ? 'flex' : 'none', flexDirection: 'column' }}>
          <AdminPage idToken={idToken} />
        </div>
      )}
    </div>
  )
}
