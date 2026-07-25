import { useState, useEffect, useRef, useCallback, KeyboardEvent } from 'react'
import { Recipe, RecipeSummary, HitlLabels, UiStrings, ShopItem, WsOutMessage, CalendarEntry, CalendarDay } from '../types'
import { WS_BASE, DEV_MODE, DEV_UID } from '../config'
import { t } from '../theme'

// The calendar grid matches day cells by exact YYYY-MM-DD string. Coerce common
// agent date forms (unpadded, day-first, year-less) so the entry actually shows.
function normalizeIsoDate(raw?: string): string | undefined {
  if (!raw) return raw
  const s = raw.trim()
  const thisYear = new Date().getFullYear()
  // The LLM sometimes emits a stale past year (e.g. 2023); a meal plan is never
  // in the past, so bump any year before the current one up to the current year.
  const fixYear = (y: string) => String(Math.max(parseInt(y, 10), thisYear))
  let m = /^(\d{4})[-/](\d{1,2})[-/](\d{1,2})$/.exec(s)       // YYYY-M-D
  if (m) return `${fixYear(m[1])}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}`
  m = /^(\d{1,2})[./](\d{1,2})[./](\d{4})$/.exec(s)            // D.M.YYYY
  if (m) return `${fixYear(m[3])}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`
  m = /^(\d{1,2})[./](\d{1,2})$/.exec(s)                       // D.M (year-less)
  if (m) return `${thisYear}-${m[2].padStart(2, '0')}-${m[1].padStart(2, '0')}`
  return s
}

interface Props {
  sessionId: string
  idToken: string | null
  useSpizarnia: boolean
  ui: UiStrings
  shopItems: ShopItem[]
  onShopItemsChange: (items: ShopItem[]) => void
  onAddToCalendar: (entry: CalendarEntry) => void
  onCalendarRemove: (entryId: string) => void
  calDays: CalendarDay[]
  onProcessingChange: (v: boolean) => void
}

type ChatMsg =
  | { kind: 'bot'; text: string }      // streaming bot response — accumulated
  | { kind: 'user'; text: string }
  | { kind: 'error'; text: string }
  | { kind: 'recipe'; recipe: Recipe; source: string }
  | { kind: 'recipe_options'; proposals: RecipeSummary[] }
  | { kind: 'hitl'; recipe: Recipe; round: number; labels: HitlLabels }
  | { kind: 'spizarnia_offer'; missing: string[]; used: string[] }

// Convert CalendarDay[] to the flat entries format the backend expects
function calendarToWsPayload(calDays: CalendarDay[]) {
  return {
    entries: calDays.flatMap(day =>
      day.recipes.map(r => ({
        id: r.id,
        date: day.date,
        recipe_name: r.recipeName,
        ingredients: r.ingredients,
      }))
    ),
  }
}

export default function ChatPanel({ sessionId, idToken, useSpizarnia, ui, shopItems, onShopItemsChange, onAddToCalendar, onCalendarRemove, calDays, onProcessingChange }: Props) {
  const [messages, setMessages] = useState<ChatMsg[]>([])
  const [input, setInput] = useState('')
  const [inputEnabled, setInputEnabled] = useState(false)
  const [status, setStatus] = useState('Inicjalizacja…')
  const [isTyping, setIsTyping] = useState(false)  // shows typing dots bubble
  const [hasRecipe, setHasRecipe] = useState(false) // true after first recipe delivered
  const wsRef = useRef<WebSocket | null>(null)
  const bottomRef = useRef<HTMLDivElement>(null)
  // Track whether last message is an in-progress bot stream
  const streamingRef = useRef(false)
  // Always-current refs for callbacks used inside the ws.onmessage closure
  const onProcessingChangeRef = useRef(onProcessingChange)
  const onAddToCalendarRef = useRef(onAddToCalendar)
  const onCalendarRemoveRef = useRef(onCalendarRemove)
  useEffect(() => { onProcessingChangeRef.current = onProcessingChange }, [onProcessingChange])
  useEffect(() => { onAddToCalendarRef.current = onAddToCalendar }, [onAddToCalendar])
  useEffect(() => { onCalendarRemoveRef.current = onCalendarRemove }, [onCalendarRemove])

  function addMsg(msg: ChatMsg) {
    setMessages(prev => [...prev, msg])
  }

  function appendBotToken(token: string) {
    setMessages(prev => {
      if (prev.length > 0 && prev[prev.length - 1].kind === 'bot') {
        const last = prev[prev.length - 1] as { kind: 'bot'; text: string }
        return [...prev.slice(0, -1), { kind: 'bot', text: last.text + token }]
      }
      return [...prev, { kind: 'bot', text: token }]
    })
  }

  function addToShop(names: string[]) {
    onShopItemsChange([
      ...shopItems,
      ...names
        .filter(n => !shopItems.some(i => i.name.toLowerCase() === n.toLowerCase()))
        .map(n => ({ name: n, checked: false })),
    ])
  }

  function mergeShopItems(msg: { items: string[]; replace: boolean; structured?: { items: { name: string; quantity: string; section: string }[]; sections: string[] } }) {
    if (msg.structured) {
      const newItems: ShopItem[] = msg.structured.items.map(i => ({ name: i.quantity ? `${i.name} — ${i.quantity}` : i.name, checked: false, section: i.section }))
      if (msg.replace) {
        onShopItemsChange(newItems)
      } else {
        onShopItemsChange([...shopItems, ...newItems.filter(n => !shopItems.some(i => i.name.toLowerCase() === n.name.toLowerCase()))])
      }
      return
    }
    const newItems: ShopItem[] = msg.items.map(n => ({ name: n, checked: false }))
    if (msg.replace) {
      onShopItemsChange(newItems)
    } else {
      addToShop(msg.items)
    }
  }

  const sendWS = useCallback((obj: object) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(obj))
    }
  }, [])

  function connectWS() {
    const params = new URLSearchParams()
    if (useSpizarnia) params.set('use_spizarnia', 'true')
    if (DEV_MODE) {
      params.set('dev_uid', DEV_UID)
    } else if (idToken) {
      // Browsers can't set headers on a WebSocket, so the Firebase ID token goes
      // in the query string; the backend accepts a `token` param.
      params.set('token', idToken)
    }
    const qs = params.toString() ? `?${params.toString()}` : ''
    const ws = new WebSocket(`${WS_BASE}/v1/ws/${sessionId}${qs}`)
    wsRef.current = ws
    ws.onopen = () => { setStatus('Połączono'); setInputEnabled(true) }
    ws.onclose = evt => { setStatus(`Rozłączono (kod ${evt.code})`); setInputEnabled(false) }
    ws.onerror = () => setStatus('Błąd WebSocket')
    ws.onmessage = evt => {
      try {
        const msg = JSON.parse(evt.data) as WsOutMessage
        if (msg.type !== 'token') console.log('[WS] raw msg:', msg.type, JSON.stringify(msg).slice(0, 200))
        handleMessage(msg)
      } catch (err) {
        console.error('[WS] parse error:', err, 'raw:', evt.data.slice(0, 200))
        appendBotToken(evt.data)
      }
    }
    return ws
  }

  useEffect(() => {
    if (!sessionId) return
    setStatus('Łączę…')
    const ws = connectWS()
    return () => ws.close()
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessionId])

  function handleMessage(msg: WsOutMessage) {
    switch (msg.type) {
      case 'token':
        appendBotToken(msg.content)
        if (!streamingRef.current) {
          streamingRef.current = true
          onProcessingChangeRef.current(true)
        }
        setInputEnabled(false)
        break
      case 'agent_update':
        setStatus(`${msg.agent}: ${msg.status}`)
        break
      case 'final_recipe':
        console.log('[WS] final_recipe received', msg.recipe?.name, msg.source)
        streamingRef.current = false
        setIsTyping(false)
        onProcessingChangeRef.current(false)
        addMsg({ kind: 'recipe', recipe: msg.recipe, source: msg.source })
        setHasRecipe(true)
        setInputEnabled(true)
        break
      case 'recipe_options':
        streamingRef.current = false
        setIsTyping(false)
        onProcessingChangeRef.current(false)
        addMsg({ kind: 'recipe_options', proposals: msg.proposals })
        setInputEnabled(true)
        setStatus('Połączono')
        break
      case 'hitl_checkpoint':
        streamingRef.current = false
        addMsg({ kind: 'hitl', recipe: msg.recipe, round: msg.round, labels: msg.labels })
        break
      case 'spizarnia_offer':
        addMsg({ kind: 'spizarnia_offer', missing: msg.missing_ingredients, used: msg.used_from_spizarnia })
        break
      case 'calendar_update':
        console.debug('[CAL] calendar_update received:', msg.action, JSON.stringify(msg.entry ?? msg.entry_id))
        if (msg.action === 'add' && msg.entry) {
          const raw = msg.entry as CalendarEntry & { recipe_name?: string; date?: string; recipe?: Recipe }
          const entry: CalendarEntry = {
            id: raw.id,
            recipeName: raw.recipe_name ?? raw.recipeName ?? '',
            ingredients: raw.ingredients,
            date: normalizeIsoDate(raw.date),
            recipe: raw.recipe ?? undefined,
          }
          console.debug('[CAL] mapped entry → date:', entry.date, 'name:', entry.recipeName, 'rawDate:', raw.date)
          onAddToCalendarRef.current(entry)
        } else if (msg.action === 'remove' && msg.entry_id) {
          onCalendarRemoveRef.current(msg.entry_id)
        }
        // After calendar side-effect, re-enable input and mark stream done
        streamingRef.current = false
        setIsTyping(false)
        onProcessingChangeRef.current(false)
        setInputEnabled(true)
        setStatus('Połączono')
        break
      case 'shopping_list_update':
        mergeShopItems(msg)
        streamingRef.current = false
        setIsTyping(false)
        onProcessingChangeRef.current(false)
        setInputEnabled(true)
        setStatus('Połączono')
        break
      case 'quota_exceeded':
        // Budget exhausted — show the server's localized message and keep the
        // input enabled so the user can try again after the window resets.
        streamingRef.current = false
        setIsTyping(false)
        onProcessingChangeRef.current(false)
        addMsg({ kind: 'error', text: msg.message })
        setInputEnabled(true)
        break
      case 'error':
        streamingRef.current = false
        setIsTyping(false)
        onProcessingChangeRef.current(false)
        addMsg({ kind: 'error', text: msg.message })
        setInputEnabled(true)
        break
    }
  }

  // When token stream ends (no more tokens for 800ms), re-enable input
  const streamTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  useEffect(() => {
    if (streamTimerRef.current) clearTimeout(streamTimerRef.current)
    const last = messages[messages.length - 1]
    const botIsStreaming = streamingRef.current && last?.kind === 'bot'
    if (botIsStreaming) {
      streamTimerRef.current = setTimeout(() => {
        streamingRef.current = false
        setIsTyping(false)
        onProcessingChangeRef.current(false)
        setInputEnabled(true)
        setStatus('Połączono')
      }, 800)
    }
    return () => { if (streamTimerRef.current) clearTimeout(streamTimerRef.current) }
  }, [messages])

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  function sendMessage() {
    const text = input.trim()
    if (!text || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) return
    addMsg({ kind: 'user', text })
    // Include current calendar state so agent can read/write it
    sendWS({ type: 'message', content: text, calendar: calendarToWsPayload(calDays) })
    setInput('')
    setInputEnabled(false)
    streamingRef.current = true
    setIsTyping(true)
    onProcessingChangeRef.current(true)
  }

  function pickRecipe(choice: number) {
    if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN || streamingRef.current) return
    addMsg({ kind: 'user', text: `wybieram ${choice}` })
    sendWS({ type: 'message', content: `wybieram ${choice}`, calendar: calendarToWsPayload(calDays) })
    setInputEnabled(false)
    streamingRef.current = true
    setIsTyping(true)
    onProcessingChangeRef.current(true)
  }

  function restartChat() {
    if (wsRef.current) {
      wsRef.current.close()
      wsRef.current = null
    }
    setMessages([])
    setInput('')
    setHasRecipe(false)
    setIsTyping(false)
    setStatus('Łączę…')
    setInputEnabled(false)
    streamingRef.current = false
    onProcessingChangeRef.current(false)
    connectWS()
  }

  function onKey(e: KeyboardEvent<HTMLInputElement>) {
    if (e.key === 'Enter') sendMessage()
  }

  const lastMsg = messages[messages.length - 1]
  const showTyping = isTyping && !(lastMsg?.kind === 'bot' && lastMsg.text.length > 0)

  return (
    <div style={styles.panel}>
      <div style={styles.statusBar}>{status}</div>
      <style>{`
        @keyframes cp-bounce {
          0%, 80%, 100% { transform: translateY(0); opacity: 0.4; }
          40%            { transform: translateY(-5px); opacity: 1; }
        }
      `}</style>
      <div style={styles.messages}>
        {messages.map((msg, i) => (
          <MessageBubble
            key={i}
            msg={msg}
            ui={ui}
            sendWS={sendWS}
            addToShop={addToShop}
            onAddToCalendar={onAddToCalendarRef.current}
            onPickRecipe={pickRecipe}
          />
        ))}
        {showTyping && (
          <div style={styles.typingBubble}>
            {[0, 1, 2].map(i => (
              <span key={i} style={{ ...styles.typingDot, animationDelay: `${i * 0.18}s` }} />
            ))}
          </div>
        )}
        <div ref={bottomRef} />
      </div>
      <div style={styles.inputRow}>
        {hasRecipe && (
          <button style={styles.restartBtn} onClick={restartChat} title="Zacznij nową rozmowę od początku">
            ↺ Nowa rozmowa
          </button>
        )}
        <input
          style={styles.input}
          type="text"
          placeholder="Napisz wiadomość… (np. 'zaproponuj mi danie na dziś', 'dodaj przepis do kalendarza na 30.05')"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={onKey}
          disabled={!inputEnabled}
        />
        <button style={{ ...styles.sendBtn, ...(inputEnabled ? {} : styles.sendBtnDisabled) }} onClick={sendMessage} disabled={!inputEnabled}>
          Wyślij
        </button>
      </div>
    </div>
  )
}

function MessageBubble({ msg, ui, sendWS, addToShop, onAddToCalendar, onPickRecipe }: {
  msg: ChatMsg
  ui: UiStrings
  sendWS: (obj: object) => void
  addToShop: (names: string[]) => void
  onAddToCalendar: (entry: CalendarEntry) => void
  onPickRecipe: (choice: number) => void
}) {
  const [locked, setLocked] = useState(false)
  const [showModify, setShowModify] = useState(false)
  const [modifyText, setModifyText] = useState('')
  const [addMissing, setAddMissing] = useState<boolean | null>(msg.kind === 'spizarnia_offer' && msg.missing.length === 0 ? false : null)
  const [removeUsed, setRemoveUsed] = useState<boolean | null>(msg.kind === 'spizarnia_offer' && msg.used.length === 0 ? false : null)

  function maybeSendOffer(am: boolean | null, ru: boolean | null, missing: string[]) {
    if (am === null || ru === null) return
    sendWS({ type: 'spizarnia_response', add_missing: am, remove_used: ru })
    setLocked(true)
    if (am && missing.length) addToShop(missing)
  }

  if (msg.kind === 'bot')   return <div style={styles.msgBot}>{msg.text}</div>
  if (msg.kind === 'user')  return <div style={styles.msgUser}>{msg.text}</div>
  if (msg.kind === 'error') return <div style={styles.msgError}>Błąd: {msg.text}</div>

  if (msg.kind === 'recipe') {
    const { recipe, source } = msg
    const calEntry: CalendarEntry = {
      id: crypto.randomUUID(),
      recipeName: recipe.name,
      ingredients: recipe.ingredients,
      recipe,
    }
    return (
      <div style={styles.recipeCard}>
        {recipe.image_url && (
          <img src={recipe.image_url} alt={recipe.name} style={styles.recipeImg}
            onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
        )}
        <div style={{ padding: 14 }}>
        <div style={styles.recipeHeader}>
          <h4 style={styles.recipeName}>{recipe.name}</h4>
          <span style={{ ...styles.badge, background: source === 'WEB_SEARCH' ? t.color.accent : t.color.textMuted }}>
            {source === 'WEB_SEARCH' ? 'web' : 'AI'}
          </span>
        </div>
        <p style={{ fontSize: '0.85rem', marginBottom: 8 }}>{recipe.description}</p>
        <strong style={{ fontSize: '0.82rem' }}>Składniki</strong>
        <ul style={styles.ul}>{recipe.ingredients.map((ing, i) => <li key={i}>{ing}</li>)}</ul>
        <strong style={{ fontSize: '0.82rem' }}>Kroki</strong>
        <ol style={styles.ul}>{recipe.steps.map((s, i) => <li key={i}>{s}</li>)}</ol>
        <div style={styles.recipeMeta}>⏱ Przygotowanie {recipe.prep_time_minutes} min · Gotowanie {recipe.cook_time_minutes} min · {recipe.difficulty} · Porcje: {recipe.servings}</div>
        <div style={styles.recipeFooter}>
          <button style={styles.calBtn} onClick={() => onAddToCalendar(calEntry)}>+ Dodaj do kalendarza</button>
          {recipe.source_url && (
            <a href={recipe.source_url} target="_blank" rel="noopener noreferrer" style={styles.sourceLink}>
              Źródło ↗
            </a>
          )}
        </div>
        </div>
      </div>
    )
  }

  if (msg.kind === 'recipe_options') {
    return (
      <div style={styles.optionsWrap}>
        <div style={styles.optionsLabel}>Oto propozycje — wybierz jedną:</div>
        <div style={styles.optionsGrid}>
          {msg.proposals.map((p, i) => (
            <div key={i} style={styles.optionCard}>
              {p.image_url
                ? <img src={p.image_url} alt={p.name} style={styles.optionImg} onError={e => { (e.target as HTMLImageElement).style.display = 'none' }} />
                : <div style={styles.optionImgPlaceholder} />
              }
              <div style={styles.optionBody}>
                <div style={styles.optionHeader}>
                  <span style={styles.optionNum}>{i + 1}</span>
                  <span style={styles.optionName}>{p.name}</span>
                  <span style={{ ...styles.badge, background: p.source === 'web_search' ? t.color.accent : t.color.textMuted }}>{p.source === 'web_search' ? 'web' : 'AI'}</span>
                </div>
                <p style={styles.optionDesc}>{p.description}</p>
                {/* Fast-path cards (STEP 47) carry no invented metadata — only render
                    what the page itself supplied, so empty chips never show as "0 min · ". */}
                {(p.total_time_minutes > 0 || p.difficulty) && (
                  <div style={styles.optionMeta}>
                    {[p.total_time_minutes > 0 ? `⏱ ${p.total_time_minutes} min` : null, p.difficulty || null]
                      .filter(Boolean).join(' · ')}
                  </div>
                )}
                {p.key_ingredients.length > 0 && (
                  <div style={styles.optionIngr}>{p.key_ingredients.slice(0, 4).join(', ')}</div>
                )}
                {p.source === 'web_search' && p.source_url && (
                  <a href={p.source_url} target="_blank" rel="noopener noreferrer" style={styles.sourceLink}>
                    Źródło ↗
                  </a>
                )}
                <button style={styles.optionBtn} onClick={() => onPickRecipe(i + 1)}>
                  Wybieram
                </button>
              </div>
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (msg.kind === 'hitl') {
    const { recipe, round, labels } = msg
    return (
      <div style={styles.hitlCard}>
        <h4 style={{ marginBottom: 6 }}>{labels.heading.replace('{round}', String(round))}</h4>
        <p style={{ color: '#555', marginBottom: 10, fontSize: '0.85rem' }}>
          <strong>{recipe.name}</strong> — {recipe.description}
        </p>
        {!locked ? (
          <>
            <div style={styles.hitlBtns}>
              <button style={styles.btnApprove} onClick={() => { sendWS({ type: 'hitl_response', approved: true }); setLocked(true) }}>{labels.approve}</button>
              <button style={styles.btnModify} onClick={() => setShowModify(true)}>{labels.modify}</button>
              <button style={styles.btnReject} onClick={() => { sendWS({ type: 'hitl_response', approved: false }); setLocked(true) }}>{labels.reject}</button>
            </div>
            {showModify && (
              <div style={styles.modifyRow}>
                <input style={styles.modifyInput} type="text" placeholder={labels.modify_placeholder} value={modifyText}
                  onChange={e => setModifyText(e.target.value)}
                  onKeyDown={e => { if (e.key === 'Enter' && modifyText.trim()) { sendWS({ type: 'hitl_response', approved: false, modification: modifyText }); setLocked(true) } }}
                  autoFocus />
                <button style={styles.btnSend} onClick={() => { if (modifyText.trim()) { sendWS({ type: 'hitl_response', approved: false, modification: modifyText }); setLocked(true) } }}>{labels.modify_send}</button>
              </div>
            )}
          </>
        ) : (
          <p style={{ color: '#888', fontSize: '0.8rem', marginTop: 8 }}>✓ Odpowiedź wysłana</p>
        )}
      </div>
    )
  }

  if (msg.kind === 'spizarnia_offer') {
    const { missing, used } = msg
    const yes = ui.spizarnia_offer_confirm ?? 'Tak'
    const no  = ui.spizarnia_offer_skip    ?? 'Nie'
    return (
      <div style={styles.offerCard}>
        <h4 style={{ marginBottom: 10, color: t.color.success }}>Spiżarnia</h4>
        {missing.length > 0 && (
          <div style={styles.offerRow}>
            <span style={{ flex: 1, fontSize: '0.83rem' }}>
              {ui.spizarnia_offer_add ?? 'Dodać brakujące do listy zakupów?'}<br />
              <small style={{ color: '#555' }}>{missing.join(', ')}</small>
            </span>
            <div style={styles.offerBtns}>
              <button style={styles.btnYes} onClick={() => { const am = true; setAddMissing(am); maybeSendOffer(am, removeUsed, missing) }} disabled={addMissing !== null || locked}>{yes}</button>
              <button style={styles.btnNo}  onClick={() => { const am = false; setAddMissing(am); maybeSendOffer(am, removeUsed, missing) }} disabled={addMissing !== null || locked}>{no}</button>
            </div>
          </div>
        )}
        {used.length > 0 && (
          <div style={styles.offerRow}>
            <span style={{ flex: 1, fontSize: '0.83rem' }}>
              {ui.spizarnia_offer_remove ?? 'Usunąć zużyte ze spiżarni?'}<br />
              <small style={{ color: '#555' }}>{used.join(', ')}</small>
            </span>
            <div style={styles.offerBtns}>
              <button style={styles.btnYes} onClick={() => { const ru = true; setRemoveUsed(ru); maybeSendOffer(addMissing, ru, missing) }} disabled={removeUsed !== null || locked}>{yes}</button>
              <button style={styles.btnNo}  onClick={() => { const ru = false; setRemoveUsed(ru); maybeSendOffer(addMissing, ru, missing) }} disabled={removeUsed !== null || locked}>{no}</button>
            </div>
          </div>
        )}
        {locked && <p style={{ color: '#888', fontSize: '0.8rem', marginTop: 8 }}>✓</p>}
      </div>
    )
  }

  return null
}

const styles: Record<string, React.CSSProperties> = {
  panel: { flex: 1, display: 'flex', flexDirection: 'column', overflow: 'hidden', background: t.color.bg },
  statusBar: { fontSize: '0.73rem', padding: '5px 18px', background: t.color.surfaceMuted, color: t.color.textMuted, borderBottom: `1px solid ${t.color.border}`, flexShrink: 0 },
  messages: { flex: 1, overflowY: 'auto', padding: 18, display: 'flex', flexDirection: 'column', gap: 12, fontSize: '0.88rem' },
  inputRow: { display: 'flex', padding: '12px 16px', gap: 8, borderTop: `1px solid ${t.color.border}`, background: t.color.surface, flexShrink: 0 },
  input: { flex: 1, border: `1px solid ${t.color.borderStrong}`, borderRadius: t.radius.md, padding: '9px 13px', fontSize: '0.88rem', outline: 'none', background: t.color.surface },
  sendBtn: { background: t.color.primary, color: '#fff', border: 'none', borderRadius: t.radius.md, padding: '9px 20px', fontSize: '0.88rem', fontWeight: 600, cursor: 'pointer', boxShadow: t.shadow.sm },
  sendBtnDisabled: { opacity: 0.45, cursor: 'default', boxShadow: 'none' },
  restartBtn: { background: t.color.surface, color: t.color.textMuted, border: `1px solid ${t.color.border}`, borderRadius: t.radius.md, padding: '9px 12px', fontSize: '0.8rem', cursor: 'pointer', flexShrink: 0, whiteSpace: 'nowrap' },

  typingBubble: { background: t.color.surfaceMuted, padding: '11px 15px', borderRadius: t.radius.lg, alignSelf: 'flex-start', display: 'flex', gap: 5, alignItems: 'center', boxShadow: t.shadow.sm },
  typingDot: { display: 'inline-block', width: 7, height: 7, borderRadius: '50%', background: t.color.textFaint, animation: 'cp-bounce 1.2s ease-in-out infinite' },

  msgBot:   { background: t.color.surface, padding: '10px 14px', borderRadius: t.radius.lg, borderTopLeftRadius: 4, maxWidth: '88%', alignSelf: 'flex-start', whiteSpace: 'pre-wrap', lineHeight: 1.55, border: `1px solid ${t.color.border}`, boxShadow: t.shadow.sm, color: t.color.text },
  msgUser:  { background: t.color.primary, color: '#fff', padding: '10px 14px', borderRadius: t.radius.lg, borderTopRightRadius: 4, maxWidth: '88%', alignSelf: 'flex-end', boxShadow: t.shadow.sm },
  msgError: { background: t.color.dangerSoft, padding: '10px 14px', borderRadius: t.radius.lg, maxWidth: '88%', alignSelf: 'flex-start', color: t.color.danger, border: `1px solid #fecaca` },

  optionsWrap: { alignSelf: 'stretch' },
  optionsLabel: { fontSize: '0.82rem', color: t.color.textMuted, marginBottom: 8 },
  optionsGrid: { display: 'grid', gridTemplateColumns: 'repeat(2, 1fr)', gap: 10 },
  optionCard: { background: t.color.surface, border: `1px solid ${t.color.border}`, borderRadius: t.radius.lg, overflow: 'hidden', display: 'flex', flexDirection: 'column', fontSize: '0.82rem', boxShadow: t.shadow.sm },
  optionImg: { width: '100%', height: 110, objectFit: 'cover', display: 'block' },
  optionImgPlaceholder: { width: '100%', height: 110, background: t.color.surfaceMuted },
  optionBody: { padding: 12, display: 'flex', flexDirection: 'column', gap: 4, flex: 1 },
  optionHeader: { display: 'flex', alignItems: 'center', gap: 6, marginBottom: 2 },
  optionNum: { background: t.color.primary, color: '#fff', borderRadius: '50%', width: 18, height: 18, display: 'flex', alignItems: 'center', justifyContent: 'center', fontSize: '0.7rem', fontWeight: 700, flexShrink: 0 },
  optionName: { fontWeight: 600, flex: 1, fontSize: '0.84rem', color: t.color.text },
  optionDesc: { color: t.color.textMuted, margin: 0, lineHeight: 1.4, fontSize: '0.8rem' },
  optionMeta: { color: t.color.textFaint, fontSize: '0.75rem' },
  optionIngr: { color: t.color.textMuted, fontSize: '0.75rem', fontStyle: 'italic' },
  optionBtn: { marginTop: 6, background: t.color.primary, color: '#fff', border: 'none', borderRadius: t.radius.sm, padding: '5px 12px', fontSize: '0.78rem', fontWeight: 600, cursor: 'pointer', alignSelf: 'flex-start' },

  recipeCard: { background: t.color.surface, border: `1px solid ${t.color.border}`, borderRadius: t.radius.lg, overflow: 'hidden', alignSelf: 'stretch', fontSize: '0.85rem', boxShadow: t.shadow.md },
  recipeImg: { width: '100%', height: 180, objectFit: 'cover', display: 'block' },
  recipeHeader: { display: 'flex', alignItems: 'baseline', gap: 10, marginBottom: 4 },
  recipeName: { margin: 0, fontSize: '1.05rem', color: t.color.text },
  badge: { background: t.color.primary, color: '#fff', borderRadius: t.radius.sm, padding: '2px 8px', fontSize: '0.7rem', fontWeight: 600, textTransform: 'uppercase', letterSpacing: 0.4 },
  recipeMeta: { color: t.color.textMuted, fontSize: '0.78rem', marginTop: 8 },
  ul: { paddingLeft: 18, margin: '6px 0', color: t.color.text },
  recipeFooter: { display: 'flex', alignItems: 'center', gap: 12, marginTop: 12 },
  calBtn: { background: t.color.primary, color: '#fff', border: 'none', borderRadius: t.radius.sm, padding: '7px 14px', fontSize: '0.8rem', fontWeight: 600, cursor: 'pointer', boxShadow: t.shadow.sm },
  sourceLink: { fontSize: '0.78rem', color: t.color.primary, textDecoration: 'none', borderBottom: `1px dotted ${t.color.primary}` },

  hitlCard: { background: t.color.primarySoft, border: `1px solid ${t.color.primaryBorder}`, borderRadius: t.radius.lg, padding: 16, alignSelf: 'stretch', fontSize: '0.85rem' },
  hitlBtns: { display: 'flex', gap: 8, flexWrap: 'wrap' },
  btnApprove: { background: t.color.success, color: '#fff', border: 'none', borderRadius: t.radius.sm, padding: '8px 15px', fontSize: '0.82rem', fontWeight: 600, cursor: 'pointer' },
  btnModify:  { background: t.color.warning, color: '#fff', border: 'none', borderRadius: t.radius.sm, padding: '8px 15px', fontSize: '0.82rem', fontWeight: 600, cursor: 'pointer' },
  btnReject:  { background: t.color.danger, color: '#fff', border: 'none', borderRadius: t.radius.sm, padding: '8px 15px', fontSize: '0.82rem', fontWeight: 600, cursor: 'pointer' },
  btnSend:    { background: t.color.primary, color: '#fff', border: 'none', borderRadius: t.radius.sm, padding: '7px 13px', fontSize: '0.82rem', fontWeight: 600, cursor: 'pointer' },
  modifyRow:  { display: 'flex', gap: 6, marginTop: 8 },
  modifyInput: { flex: 1, border: `1px solid ${t.color.borderStrong}`, borderRadius: t.radius.sm, padding: '7px 11px', fontSize: '0.82rem', outline: 'none' },

  offerCard: { background: t.color.successSoft, border: `1px solid ${t.color.successBorder}`, borderRadius: t.radius.lg, padding: 16, alignSelf: 'stretch', fontSize: '0.85rem' },
  offerRow:  { display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8, gap: 10 },
  offerBtns: { display: 'flex', gap: 6 },
  btnYes: { background: t.color.success, color: '#fff', border: 'none', borderRadius: t.radius.sm, padding: '6px 13px', fontSize: '0.8rem', fontWeight: 600, cursor: 'pointer' },
  btnNo:  { background: t.color.surface, color: t.color.textMuted, border: `1px solid ${t.color.borderStrong}`, borderRadius: t.radius.sm, padding: '6px 13px', fontSize: '0.8rem', cursor: 'pointer' },
}
