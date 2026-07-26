# frontend — widget + test site

> Root context: [CLAUDE.md](../CLAUDE.md). Two things live here: the **embeddable
> chat widget** (`widget.js`, the product) and a **Vite + React + TS test app**
> (`src/`, a mock cooking site that hosts the widget for local dev).

## Layout

```
frontend/
├── widget.js          # embeddable chat widget — the shipped artifact; adds Authorization header
├── vite.config.ts     # dev server on port 3000
└── src/               # test-app only (NOT shipped)
    ├── App.tsx        # mock cooking-site shell
    ├── main.tsx       # entry
    ├── firebase.ts    # real Firebase Auth client
    ├── config.ts      # API base URL, API key
    ├── theme.ts / types.ts
    ├── components/     # ChatPanel, CalendarPage, ShoppingList, FriscoPanel,
    │                   # SpizarniaPanel, SourcesPage, AdminPage, ChangePassword,
    │                   # Login, NavBar
    ├── hooks/          # useSpizarnia · useCalendar (server-side plan, STEP 52)
    └── lib/            # pure logic, each with a sibling .test.ts:
                        #   shoppingList.ts · calendar.ts (slot move/selection
                        #   reducers + daysFromServer/daysToServer) ·
                        #   servings.ts (portionsLabel)
```

## Conventions

- **`widget.js` is the product; `src/` is only a test harness.** Keep them
  separate — don't let test-app React code leak into the standalone widget.
- The widget talks to the client app's `/v1` REST + WebSocket API; it attaches the
  `Authorization: Bearer <firebase-id-token>` header. WS message shapes are the
  server's `cookbot/protocols/ws_messages.py` — keep the client in sync with it.
- Dev server: `npm run dev` → http://localhost:3000 (talks to the client app on :8000).
- **Keep testable logic out of the components.** Reducers and formatters live in
  `src/lib/` as pure functions so they can be tested without a DOM — there is no
  component-testing setup (no jsdom, no React Testing Library), so logic left
  inside a `.tsx` is effectively untested.
- Tests are **`node:test` run through `tsx`**, not Vitest: `npm test` →
  `tsx --test src/lib/*.test.ts`. Import `test`/`assert` from `node:test` /
  `node:assert`, and note the glob only picks up `src/lib/` — a test file
  elsewhere is silently never run.
- Type check with `npx tsc --noEmit` (the build runs `tsc -b && vite build`).
