import { useState, useEffect, useCallback } from 'react'
import { Panel, PanelGroup, PanelResizeHandle } from 'react-resizable-panels'
import Login from './components/Login'
import NavBar from './components/NavBar'
import SpizarniaPanel from './components/SpizarniaPanel'
import ShoppingList from './components/ShoppingList'
import ChatPanel from './components/ChatPanel'
import CalendarPage from './components/CalendarPage'
import SourcesPage from './components/SourcesPage'
import { useSpizarnia, authHeaders } from './hooks/useSpizarnia'
import { Page, UiStrings, ShopItem, CalendarDay, CalendarEntry } from './types'
import { API_BASE, TEST_USER } from './config'

const CAL_KEY = 'tastyhub_calendar'

function loadCalendar(): CalendarDay[] {
  try { return JSON.parse(localStorage.getItem(CAL_KEY) ?? '[]') } catch { return [] }
}
function saveCalendar(days: CalendarDay[]) {
  localStorage.setItem(CAL_KEY, JSON.stringify(days))
}

export default function App() {
  const [loggedIn, setLoggedIn]   = useState(false)
  const [idToken, setIdToken]     = useState<string | null>(null)
  const [sessionId, setSessionId] = useState<string>('')
  const [page, setPage]           = useState<Page>('chat')
  const [ui, setUi]               = useState<UiStrings>({})
  const [spizEnabled, setSpizEnabled] = useState(false)
  const [shopItems, setShopItems] = useState<ShopItem[]>([])
  const [calDays, setCalDays]     = useState<CalendarDay[]>(loadCalendar)
  const [chatProcessing, setChatProcessing] = useState(false)

  const { items: spizItems, load: loadSpiz, add: addSpiz, remove: removeSpiz } = useSpizarnia(idToken)

  useEffect(() => {
    fetch(`${API_BASE}/v1/ui-strings`)
      .then(r => r.json())
      .then(setUi)
      .catch(() => {})
  }, [])

  const createSession = useCallback(async (token: string | null) => {
    const resp = await fetch(`${API_BASE}/v1/sessions`, { method: 'POST', headers: authHeaders(token) })
    if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
    const data = await resp.json()
    setSessionId(data.session_id)
  }, [])

  async function handleLogin(token: string | null) {
    setIdToken(token)
    await createSession(token)
    await loadSpiz()
    setLoggedIn(true)
  }

  // Dev convenience: when VITE_TEST_USER=true, auto-login as the dev user so the
  // login screen is skipped during local smoke-testing. Runs once on mount.
  useEffect(() => {
    if (TEST_USER && !loggedIn) {
      handleLogin(null).catch(() => {})
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  function handleLogout() {
    setLoggedIn(false)
    setIdToken(null)
    setSessionId('')
    setPage('chat')
  }

  function handleCalendarChange(days: CalendarDay[]) {
    setCalDays(days)
    saveCalendar(days)
  }

  // Functional-updater variant — safe to call from the WS message closure in
  // ChatPanel, which may hold a stale `calDays` snapshot. Always works off the
  // latest state, then persists it.
  function updateCalendar(fn: (prev: CalendarDay[]) => CalendarDay[]) {
    setCalDays(prev => {
      const next = fn(prev)
      saveCalendar(next)
      return next
    })
  }

  function handleAddToCalendar(entry: CalendarEntry) {
    const targetDate = entry.date ?? new Date().toISOString().slice(0, 10)
    console.debug('[CAL] handleAddToCalendar entry:', entry.recipeName, '@', targetDate, 'id:', entry.id)
    updateCalendar(prev => {
      const existing = prev.find(d => d.date === targetDate)
      let next: CalendarDay[]
      if (existing) {
        if (existing.recipes.some(r => r.id === entry.id)) {
          next = prev  // already there
        } else {
          next = prev.map(d => d.date === targetDate ? { ...d, recipes: [...d.recipes, entry] } : d)
        }
      } else {
        next = [...prev, { date: targetDate, recipes: [entry], freeText: '' }]
      }
      console.debug('[CAL] calDays after add:', next.map(d => `${d.date}:${d.recipes.length}`).join(', '))
      return next
    })
    setPage('calendar')
  }

  function handleCalendarRemove(entryId: string) {
    updateCalendar(prev =>
      prev.map(d => ({ ...d, recipes: d.recipes.filter(r => r.id !== entryId) }))
    )
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

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', fontFamily: 'sans-serif', background: '#fffdf8' }}>
      <NavBar page={page} onNavigate={setPage} onLogout={handleLogout} ui={ui} chatProcessing={chatProcessing} />

      {/* Chat page — always mounted to preserve WebSocket + message history */}
      <div style={{ flex: 1, overflow: 'hidden', display: page === 'chat' ? 'flex' : 'none' }}>
        {sessionId && (
          <PanelGroup direction="horizontal" style={{ flex: 1, overflow: 'hidden' }}>
            <Panel defaultSize={28} minSize={18} maxSize={50} style={{ display: 'flex', flexDirection: 'column', background: '#fff', borderRight: '1px solid #e8e0d8', overflow: 'hidden' }}>
              <PanelGroup direction="vertical">
                <Panel defaultSize={55} minSize={25} style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                  <SpizarniaPanel
                    items={spizItems}
                    useSpizarnia={spizEnabled}
                    onToggle={setSpizEnabled}
                    onAdd={addSpiz}
                    onRemove={removeSpiz}
                    ui={ui}
                  />
                </Panel>
                <PanelResizeHandle style={{ height: 4, background: '#e8e0d8', cursor: 'row-resize' }} />
                <Panel defaultSize={45} minSize={20} style={{ overflow: 'hidden', display: 'flex', flexDirection: 'column' }}>
                  <ShoppingList items={shopItems} onChange={setShopItems} ui={ui} />
                </Panel>
              </PanelGroup>
            </Panel>

            <PanelResizeHandle style={{ width: 5, background: '#e8e0d8', cursor: 'col-resize' }} />

            <Panel minSize={40} style={{ display: 'flex', flexDirection: 'column', overflow: 'hidden' }}>
              <ChatPanel
                sessionId={sessionId}
                useSpizarnia={spizEnabled}
                ui={ui}
                shopItems={shopItems}
                onShopItemsChange={setShopItems}
                onAddToCalendar={handleAddToCalendar}
                onCalendarRemove={handleCalendarRemove}
                calDays={calDays}
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
        />
      </div>
    </div>
  )
}
