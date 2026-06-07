# CookBot SaaS — GCP Architecture

## Design Principles

**Per-client Cloud Run service, shared core library:**
- Cost attribution via `client_id` GCP labels — per-client billing breakdown
- Isolation — one client's traffic spike or config change doesn't affect others
- `cookbot-core` is a pip-extractable library; zero changes needed per new client
- One-way dependency: `clients/` → `cookbot-core` → GCP SDKs. Never reversed.

---

## GCP Services

| Service | Role | Phase | Est. cost |
|---|---|---|---|
| **Cloud Run** | One service per client app | MVP | ~$0 (scale-to-zero) |
| **Firestore** | Session history, HITL checkpoint persistence | MVP | ~$0 (free tier) |
| **Cloud Storage** | widget.js hosting | MVP | ~$0 (free tier) |
| **Secret Manager** | API keys per client | MVP | ~$0 |
| **Artifact Registry** | Docker images | MVP | ~$0 (0.5GB free) |
| **Cloud Build** | CI/CD: build → push → deploy | MVP | ~$0 (120 min/day free) |
| **Cloud Monitoring** | Per-client cost tracking via labels | MVP | ~$0 |
| **Cloud SQL (PostgreSQL + pgvector)** | Client-specific recipe KB | Phase 2 | ~$10/mo |
| **Cloud Run Jobs** | Nightly recipe indexer | Phase 2 | ~$0 (per run) |
| **Memorystore (Redis)** | Session state at scale (>50 concurrent) | Phase 2 | +$35/mo |
| **Cloud CDN + LB** | widget.js global delivery | Phase 2 | +$18/mo |

---

## System Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                       CLIENT WEBSITES                            │
│  cooking-site.com          recipes-hub.io        foodblog.pl     │
│  <script data-api-key>     <script data-api-key> <script>        │
└──────────┬─────────────────────────┬────────────────────────────┘
           │ WSS / HTTPS             │ WSS / HTTPS
           ▼                         ▼
┌──────────────────────┐   ┌───────────────────────┐
│ Cloud Run            │   │ Cloud Run             │  ...
│ cookbot-tastyhub     │   │ cookbot-recipeshub    │
│                      │   │                       │
│ FastAPI              │   │ FastAPI               │
│  POST /v1/sessions   │   │  POST /v1/sessions    │
│  WS   /v1/ws/{id}    │   │  WS   /v1/ws/{id}     │
│  GET  /v1/ui-strings │   │  GET  /v1/ui-strings  │
│  GET  /v1/spizarnia  │   │  ...                  │
│                      │   │                       │
│ ← imports cookbot-core   │ ← imports cookbot-core│
│ ← TastyHub config    │   │ ← RecipesHub config   │
└──────────┬───────────┘   └──────────┬────────────┘
           │                          │
           ▼                          ▼
┌──────────────────────────────────────────────────────────────────┐
│               packages/cookbot-core  (shared library)            │
│                                                                  │
│  ChatAgent (per-connection)                                      │
│  ├── update_onboarding tool  — collects 5 intake fields          │
│  ├── find_recipe tool                                            │
│  │   ├── WebSearchAgent      — search web for real recipe        │
│  │   └── RecipeGenAgent      — AI fallback when search fails     │
│  ├── add_to_calendar tool                                        │
│  ├── remove_from_calendar tool                                   │
│  └── get_shopping_list tool                                      │
│                                                                  │
│  HITL checkpoint persistence — restore pending checkpoint on     │
│                                reconnect (hitl/persistence.py)    │
│                                                                  │
│  Firestore service           — session history, HITL state       │
│  WS message protocol         — typed Pydantic schemas            │
└──────────────────────────────────────────────────────────────────┘
           │
           ▼
┌──────────────────────┐    ┌───────────────────────────────────┐
│    Firestore         │    │  Cloud SQL (PostgreSQL + pgvector) │
│                      │    │  Phase 2 only                      │
│ sessions/            │    │                                    │
│  {tenant_id}/        │    │  schema: client_tastyhub           │
│    {session_id}/     │    │    recipes (id, embedding, json)   │
│      messages[]      │    │  schema: client_recipeshub         │
│      hitl_state      │    │    recipes (id, embedding, json)   │
└──────────────────────┘    └───────────────────────────────────┘
```

---

## Chat Agent — Connection Lifecycle

```
WebSocket connect
│
├── build_chat_agent(config)      ← one agent instance per connection
├── deps = ChatAgentDeps(config)  ← holds onboarding state + history
├── message_history = []          ← grows each turn via result.new_messages()
│
└── while True:
    │   msg = await receive()
    │   deps.calendar = msg.calendar      ← refreshed from frontend each turn
    │   deps.calendar_adds = []           ← reset per-turn
    │   deps.calendar_removes = []
    │   deps.shopping_list_items = None
    │
    │   async with stream_chat_response(agent, deps, history, text) as tokens:
    │       stream tokens to WS client
    │   ← history updated in-place after block
    │
    └── emit calendar/shopping-list side-effects to WS client
```

**Why per-connection deps matters:**
`deps.onboarding` accumulates the 5 intake fields across turns. If deps were
recreated each message (the previous bug), onboarding state would be lost and
the agent would restart question 1 every time.

---

## Onboarding Flow

```
Turn 1: user sends first message
  │
  ├── _onboarding_status() injects current state into system prompt
  │   (shows what's collected, what's missing, which field to record next)
  │
  ├── Agent calls update_onboarding(dish_type=...) → mutates deps.onboarding
  │
  └── Agent asks next question (servings)

Turn 2..5: same pattern for servings, time, ingredients, notes

Turn 5 (final field recorded):
  └── update_onboarding returns complete=true
      → Agent calls find_recipe(...)
          ├── WebSearchAgent.run()  → Recipe | None
          └── RecipeGenAgent.run() if None  → Recipe
      → Streams recipe description tokens
```

If the user's first message already contains enough information (e.g. "make me
pasta for 2 in 30 minutes"), the agent fills multiple fields at once and calls
find_recipe immediately.

---

## WebSocket Message Protocol

```python
# Server → Client
{"type": "token",            "content": "..."}              # streaming text
{"type": "agent_update",     "agent": "...", "status": "..."} 
{"type": "hitl_checkpoint",  "recipe": {...}, "round": 1}
{"type": "final_recipe",     "recipe": {...}, "source": "..."}
{"type": "calendar_update",  "action": "add"|"remove", "entry": {...}}
{"type": "shopping_list_update", "items": [...], "replace": bool}
{"type": "spizarnia_offer",  "missing_ingredients": [...], "used_from_spizarnia": [...]}
{"type": "error",            "message": "..."}

# Client → Server
{"type": "message",          "content": "...", "calendar": {...}}
{"type": "hitl_response",    "approved": true}
{"type": "hitl_response",    "approved": false, "modification": "make it vegan"}
{"type": "spizarnia_response", "add_missing": true, "remove_used": false}
```

---

## Monorepo Structure

```
cookbot/
├── packages/
│   └── cookbot-core/                  ← shared library (your IP)
│       ├── cookbot/
│       │   ├── agents/
│       │   │   ├── chat.py            ★ ChatAgent + OnboardingState
│       │   │   ├── web_search.py      ★ WebSearchAgent (real recipes)
│       │   │   ├── recipe_gen.py      ★ RecipeGenAgent (AI fallback)
│       │   │   ├── refinement.py      ★ RefinementAgent (HITL modify)
│       │   │   ├── ingredient.py      ★ IngredientAgent (parse fridge)
│       │   │   ├── intake.py          ★ IntakeAgent (legacy, kept for tests)
│       │   │   └── recipe_search.py   ○ Phase 2 — pgvector search
│       │   ├── hitl/
│       │   │   ├── gate.py            ★ asyncio.Queue suspend/resume
│       │   │   ├── models.py          ★ HITLCheckpoint, HITLResponse
│       │   │   └── persistence.py     ★ Firestore checkpoint save/load
│       │   ├── models/
│       │   │   ├── recipe.py          ★ UserIntent, Recipe, etc.
│       │   │   ├── session.py         ★ Session, SessionStatus
│       │   │   ├── calendar.py        ★ CalendarEntry, CalendarState
│       │   │   ├── spizarnia.py       ★ Spizarnia, SpizarniaItem
│       │   │   ├── tenant.py          ★ TenantConfig
│       │   │   └── ui_strings.py      ★ UiStrings per language
│       │   ├── services/
│       │   │   ├── firestore.py       ★ AsyncFirestoreService
│       │   │   └── vector_search.py   ○ Phase 2
│       │   └── protocols/
│       │       └── ws_messages.py     ★ typed WS message schema + send helpers
│       └── pyproject.toml
│
├── clients/
│   └── tastyhub/
│       ├── app/
│       │   ├── main.py                ★ FastAPI lifespan, CORS, routers
│       │   ├── api/
│       │   │   ├── sessions.py        ★ POST /v1/sessions
│       │   │   ├── spizarnia.py       ★ GET/PUT/DELETE /v1/spizarnia
│       │   │   ├── ui_strings.py      ★ GET /v1/ui-strings
│       │   │   └── websocket.py       ★ WS /v1/ws/{session_id}
│       │   ├── config/
│       │   │   ├── settings.py        ★ Settings (pydantic-settings)
│       │   │   └── tenant.py          ★ TASTYHUB_CONFIG
│       │   └── middleware/
│       │       └── auth.py            ★ API key + Firebase auth
│       ├── Dockerfile
│       ├── cloudbuild.yaml
│       └── pyproject.toml
│
├── frontend/                          ← React/Vite SPA (dev harness)
│   ├── src/
│   │   ├── App.tsx                    main layout, routing
│   │   ├── components/
│   │   │   ├── ChatPanel.tsx          WS chat, streaming tokens
│   │   │   ├── SpizarniaPanel.tsx     pantry items
│   │   │   ├── ShoppingList.tsx       shopping list
│   │   │   ├── CalendarPage.tsx       meal calendar + recipe modal
│   │   │   ├── NavBar.tsx
│   │   │   └── Login.tsx
│   │   ├── hooks/
│   │   │   └── useSpizarnia.ts
│   │   ├── types.ts                   all TypeScript types
│   │   └── config.ts                  API_BASE, WS_BASE, DEV_UID
│   └── vite.config.ts
│
├── infrastructure/
│   └── terraform/                     ○ Phase 2
│
├── docker-compose.yml                 local dev (api + firestore emulator)
└── .env.example
```

---

## Per-Client Cost Attribution

```yaml
# Cloud Run service labels
labels:
  client_id: tastyhub
  env: production
  app: cookbot
```

GCP Billing → Cost Breakdown → filter `client_id=tastyhub` → exact per-client cost.

---

## MVP Cost Estimate (per client/month)

Assumptions: 500 sessions, 8 agent calls/session, 2K tokens/call.

| Item | Cost |
|---|---|
| Cloud Run (scale-to-zero) | ~$0 |
| Firestore (free tier) | ~$0 |
| OpenAI GPT-4o-mini (500×8×2K tokens) | ~$1.20 |
| OpenAI web search (500×3 calls) | ~$0.75 |
| **Total** | **~$2/month** |

---

## MVP Build Checklist

- [x] cookbot-core: models, agents, HITL gate, Firestore service, WS protocol
- [x] ChatAgent with guided onboarding (5 questions) + free-chat mode
- [x] tastyhub client: FastAPI, sessions, WS, spizarnia, auth
- [x] React/Vite frontend: chat, pantry, shopping list, calendar
- [x] docker-compose (Firestore emulator)
- [ ] Dockerfile + Cloud Build + Cloud Run deploy (Step 19-20)
- [ ] Secret Manager integration

## Phase 2 Backlog

- Cloud SQL + pgvector schema
- TastyHub recipe indexer (crawl sitemap → embed → store)
- RecipeSearchAgent using pgvector
- NutritionAgent
- Rate limiting (Redis)
- Cloud Monitoring dashboards
- Terraform infra-as-code
- Second client onboarding
