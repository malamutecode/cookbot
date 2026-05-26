# 🍳 CookBot SaaS — GCP Architecture & Build Plan
## Revised: Per-client containers, shared core library, cost-optimized

---

## On Your Approach — I Agree

Your instinct is correct and aligns well with production best practices:

**Per-client container (Cloud Run service) wins on:**
- **Cost attribution**: GCP Monitoring `client_id` labels → per-client billing breakdown natively
- **Isolation**: one client's traffic spike or config change doesn't affect others
- **Customization**: client-specific recipe knowledge base, persona, language, scaling config
- **Compliance**: client recipe data stays in their own DB schema / service identity
- **Pricing model**: you can charge per-client based on actual observed GCP costs

**Shared core library wins on:**
- Single place to fix bugs, improve agents, update PydanticAI
- Extractable to pip package when you have 3+ clients
- Forces clean separation of "your IP" vs "client integration glue"

---

## GCP Services — Selected for Cost-Effectiveness

| Service | Role | Phase | Est. cost |
|---|---|---|---|
| **Cloud Run** | One service per client app | MVP | ~$0 (scale-to-zero) |
| **Firestore** | Session history, HITL checkpoint persistence | MVP | ~$0 (free tier) |
| **Cloud Storage** | widget.js hosting | MVP | ~$0 (free tier) |
| **Cloud SQL (PostgreSQL + pgvector)** | Client-specific recipe KB | Phase 2 | ~$10/mo shared |
| **Cloud Run Jobs** | Nightly recipe indexer (crawl→embed→store) | Phase 2 | ~$0 (billed per run) |
| **Secret Manager** | API keys, DB passwords per client | MVP | ~$0 |
| **Artifact Registry** | Docker images | MVP | ~$0 (0.5GB free) |
| **Cloud Build** | CI/CD: build → push → deploy | MVP | ~$0 (120min/day free) |
| **Cloud Monitoring** | Per-client cost tracking via labels | MVP | ~$0 (basic free) |
| **Memorystore (Redis)** | Replace Firestore if HITL latency matters | Phase 2 | +$35/mo |
| **Cloud CDN + LB** | widget.js global delivery, custom domain | Phase 2 | +$18/mo |
| **Vertex AI Vector Search** | Replace pgvector at scale | Phase 2+ | $100+/mo |

**Why NOT Memorystore in MVP:**
Firestore is serverless with no minimum cost. Redis Memorystore has a ~$35/mo floor per instance.
For HITL state (a few KB, updated 2-3 times per session) Firestore read/write latency (~20-50ms) is perfectly acceptable.
Add Redis only when you have >50 concurrent active sessions or rate limiting at scale.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────────┐
│                         CLIENT WEBSITES                              │
│                                                                      │
│  cooking-site.com          recipes-hub.io          foodblog.pl       │
│  <script data-api-key>     <script data-api-key>   <script>          │
└────────────┬───────────────────────────┬────────────────────────────┘
             │ WSS / HTTPS               │ WSS / HTTPS
             ▼                           ▼
┌────────────────────────┐   ┌──────────────────────────┐
│  Cloud Run             │   │  Cloud Run               │   ...
│  cookbot-tastyhub      │   │  cookbot-recipeshub      │
│                        │   │                          │
│  FastAPI app           │   │  FastAPI app             │
│  ↳ imports cookbot-core│   │  ↳ imports cookbot-core  │
│  ↳ TastyHub config     │   │  ↳ RecipesHub config     │
│  ↳ TastyHub indexer    │   │  ↳ RecipesHub indexer    │
│  scale-to-zero         │   │  scale-to-zero           │
└────────────┬───────────┘   └────────────┬─────────────┘
             │                            │
             ▼                            ▼
┌──────────────────────────────────────────────────────────────────────┐
│                    packages/cookbot-core  (shared library)           │
│                                                                      │
│  SessionOrchestrator                                                 │
│  IngredientAgent ──→ WebSearchAgent ──→ RecipeGenAgent               │
│                      (web search tool;    (fallback if no            │
│                       returns best match   web result found)         │
│                              ↓                                       │
│                    ┌─────────────────┐                               │
│                    │  HITL Gate      │  ← asyncio.Queue pair         │
│                    │  (suspends,     │  ← persisted to Firestore     │
│                    │   awaits human) │                               │
│                    └────────┬────────┘                               │
│                             ↓                                        │
│                    RefinementAgent (if modified)                     │
│                             ↓                                        │
│                    NutritionAgent           (Phase 2)                │
└────────────────────────────────────────────────────────────────────┘
             │                            │
     ┌───────┘                            └──────┐
     ▼                                           ▼
┌─────────────────┐                  ┌────────────────────────────────┐
│    Firestore    │                  │   Cloud SQL (PostgreSQL)        │
│                 │                  │   + pgvector extension          │
│ sessions/       │                  │                                 │
│  {client_id}/   │                  │  schema: client_tastyhub        │
│    {session_id} │                  │    recipes (id, embedding, data)│
│      messages[] │                  │  schema: client_recipeshub      │
│      hitl_state │                  │    recipes (id, embedding, data)│
└─────────────────┘                  └────────────────────────────────┘
                                          (Phase 2 — not in MVP)
                                     ┌─────────────────┐
                                     │ Cloud Run Job   │
                                     │ recipe-indexer  │
                                     │ (nightly cron)  │
                                     │ Phase 2 only    │
                                     └─────────────────┘
```

---

## Monorepo Structure

```
cookbot/                                  ← git monorepo
│
├── packages/
│   └── cookbot-core/                     ← extractable library
│       ├── cookbot/
│       │   ├── agents/
│       │   │   ├── ingredient.py         ★ MVP
│       │   │   ├── web_search.py         ★ MVP — web search before AI generation
│       │   │   ├── recipe_gen.py         ★ MVP — fallback when search finds nothing
│       │   │   ├── refinement.py         ★ MVP
│       │   │   ├── recipe_search.py      ○ Phase 2 — pgvector search
│       │   │   └── nutrition.py          ○ Phase 2
│       │   ├── hitl/
│       │   │   ├── gate.py               ★ MVP — asyncio.Queue HITL logic
│       │   │   ├── models.py             ★ MVP — HITLCheckpoint, HITLResponse
│       │   │   └── persistence.py        ★ MVP — Firestore checkpoint save/load
│       │   ├── models/
│       │   │   ├── recipe.py             ★ MVP
│       │   │   ├── session.py            ★ MVP
│       │   │   └── tenant.py             ★ MVP — TenantConfig dataclass
│       │   ├── orchestrator/
│       │   │   └── session.py            ★ MVP — SessionOrchestrator
│       │   ├── services/
│       │   │   ├── firestore.py          ★ MVP — history + HITL state
│       │   │   ├── vector_search.py      ○ Phase 2 — pgvector wrapper
│       │   │   └── redis.py              ○ Phase 2
│       │   └── protocols/
│       │       └── ws_messages.py        ★ MVP — typed WS message schema
│       └── pyproject.toml                ← name = "cookbot-core"
│
├── clients/
│   └── tastyhub/                         ← example client app
│       ├── app/
│       │   ├── main.py                   ★ MVP — FastAPI entry point
│       │   ├── api/
│       │   │   ├── sessions.py           ★ MVP — POST /v1/sessions
│       │   │   └── websocket.py          ★ MVP — WS /v1/ws/{session_id}
│       │   ├── config/
│       │   │   └── tenant.py             ★ MVP — TastyHub TenantConfig
│       │   ├── indexer/
│       │   │   └── recipes.py            ○ Phase 2 — crawl tastyhub sitemap
│       │   └── middleware/
│       │       └── auth.py               ★ MVP — API key validation
│       ├── Dockerfile
│       ├── cloudbuild.yaml
│       ├── pyproject.toml                ← deps: cookbot-core (local path)
│       └── .env.example
│
├── frontend/                             ← test harness only
│   ├── index.html                        ★ MVP — mock cooking website
│   └── widget.js                         ★ MVP — embeddable JS snippet
│
├── infrastructure/
│   ├── terraform/                        ○ Phase 2
│   └── scripts/
│       └── new_client.sh                 ○ Phase 2 — scaffold new client
│
└── docker-compose.yml                    ★ MVP — local dev

RULE: cookbot-core NEVER imports from clients/. Direction is one-way only.
```

---

## Critical Path — MVP

### What you MUST build to have a working testable product:

```
[1]  TenantConfig model         →  drives all agent + API behavior
[2]  FastAPI app (tastyhub)     →  POST /sessions + WS endpoint
     └─ API key auth middleware
[3]  WebSocket message protocol →  typed message schema (ws_messages.py)
[4]  SessionOrchestrator        →  spawns pipeline task, bridges WS ↔ agents
[5]  asyncio.Queue HITL gate    →  pipeline suspension + resume logic
[6]  Firestore persistence      →  HITL checkpoint survives restart
[7]  IngredientAgent            →  parse fridge input
[8]  WebSearchAgent             →  search web for matching recipe (PydanticAI tool)
[9]  RecipeGenAgent             →  generate recipe when search returns nothing
[10] RefinementAgent            →  apply human modification
[11] Simple test frontend       →  mock site + widget.js for manual testing
[12] docker-compose.yml         →  local dev (api + firestore emulator)
[13] Cloud Run deployment       →  one service for tastyhub
```

### What is deliberately deferred:

- pgvector / Cloud SQL recipe knowledge base (replaced by web search in MVP)
- Recipe indexer / crawler
- NutritionAgent
- Cloud CDN / Load Balancer (use Cloud Run URL directly)
- Memorystore Redis
- Terraform / infra-as-code
- Rate limiting (add when you have real clients)
- Automated nightly indexer
- new_client.sh scaffold script
- Vertex AI Vector Search

---

## WebSocket Message Protocol

```python
# Server → Client
{"type": "token",          "content": "Let me check..."}        # streaming
{"type": "agent_update",   "agent": "RecipeSearchAgent", "status": "running"}
{"type": "hitl_checkpoint","recipe": {...Recipe...}, "round": 1}
{"type": "final_recipe",   "recipe": {...}, "source": "tastyhub|ai_generated"}
{"type": "error",          "message": "..."}

# Client → Server
{"type": "message",        "content": "I have chicken, spinach, garlic"}
{"type": "hitl_response",  "approved": true}
{"type": "hitl_response",  "approved": false, "modification": "make it vegan"}
```

---

## Per-Client Cost Attribution (GCP)

Every Cloud Run service, Cloud SQL connection, and Firestore read is tagged:

```yaml
# Cloud Run service labels (tastyhub)
labels:
  client_id: tastyhub
  env: production
  app: cookbot
```

In GCP Billing → Cost Breakdown → filter by `client_id=tastyhub` → exact cost for that client.
This lets you price per-client, invoice accurately, and identify which client is expensive.

---

## MVP Cost Estimate (per client, per month)

Assumptions: 500 chat sessions/month, 8 agent calls/session, 2K tokens/call.

| Item | Cost |
|---|---|
| Cloud Run (scale-to-zero) | ~$0 |
| Firestore (within free tier) | ~$0 |
| Cloud Storage | ~$0 |
| OpenAI GPT-4o-mini (500×8×2K tokens) | ~$1.20 |
| OpenAI web search tool calls (500×~3 searches) | ~$0.75 |
| **Total** | **~$2/mo per client** |

> Phase 2 will add Cloud SQL (~$7–10/mo shared) once the recipe KB is built.

At $50-100/mo per client SaaS fee → healthy margin from day one.

---

## Build Plan

### Phase 1 — MVP (Weeks 1–3)

**Week 1: Foundation**
- [ ] Monorepo scaffold (packages/cookbot-core + clients/tastyhub)
- [ ] TenantConfig, Recipe, Session Pydantic models (cookbot-core)
- [ ] FastAPI skeleton: POST /v1/sessions, WS /v1/ws/{session_id}
- [ ] API key auth middleware
- [ ] Firestore service (save/load session history + HITL state)
- [ ] docker-compose (Firestore emulator only — no postgres)

**Week 2: Agents + HITL**
- [ ] IngredientAgent (PydanticAI, cookbot-core)
- [ ] WebSearchAgent — PydanticAI web search tool (cookbot-core)
- [ ] RecipeGenAgent with persona injection — fallback (cookbot-core)
- [ ] RefinementAgent (cookbot-core)
- [ ] SessionOrchestrator with asyncio.Queue HITL gate (cookbot-core)
- [ ] HITL persistence to Firestore (checkpoint save/load)
- [ ] WS message protocol + streaming tokens

**Week 3: Integration + Deployment**
- [ ] Simple test frontend (index.html + widget.js)
- [ ] Dockerfile for tastyhub client
- [ ] Cloud Build → Artifact Registry → Cloud Run deploy
- [ ] Secret Manager integration
- [ ] End-to-end manual test: fridge input → recipe → HITL → final

### Phase 2 — Production Hardening (Weeks 4–6)

- [ ] Cloud SQL + pgvector schema + migration
- [ ] TastyHub recipe indexer (crawl sitemap → embed → pgvector)
- [ ] RecipeSearchAgent + pgvector tool (replace/complement WebSearchAgent)
- [ ] Cloud Scheduler trigger for nightly recipe indexer
- [ ] Rate limiting (Firestore counters or upgrade to Redis)
- [ ] Cloud Monitoring dashboards with client_id label filters
- [ ] NutritionAgent (cookbot-core)
- [ ] new_client.sh scaffold script
- [ ] Terraform for Cloud Run + Cloud SQL + Firestore
- [ ] Integration tests (pytest + httpx + websockets)
- [ ] Second client onboarding (proves the library pattern works)
