# clients/tastyhub — client app

> A thin FastAPI app that imports `cookbot-core` and adds TastyHub-specific
> config. Root context: [CLAUDE.md](../../../CLAUDE.md). **Architecture Rule 1:
> nothing here may be imported by `cookbot-core`** — the dependency is one-way.

## Layout

```
app/
├── main.py        # FastAPI entry point — all routers mounted under /v1
├── api/           # sessions, admin, spizarnia, search_prefs, shopping_list,
│                  # grocery, ui, websocket
├── config/        # Settings (env) + the TastyHub TenantConfig instance
├── middleware/    # API key auth + Firebase token auth
└── indexer/       # Phase 2 stub — empty
```

`config/tenant.py` holds the one `TenantConfig` that drives every agent — this is
where per-agent models, persona, language, UI labels, and `delivery_shops` are set.

## Firestore key pattern

```
sessions/{tenant_id}/sessions/{session_id}       # subcollection, one doc per session
  → session_id, tenant_id, uid, status, created_at, expires_at
  → messages: list[Message]
  → hitl_checkpoint: HITLCheckpoint | absent
  → chat_state: ChatState dump | absent          # resumable conversation snapshot

users/{uid}                                      # UserRecord lives on the parent doc
users/{uid}/spizarnia/items                      # pantry
users/{uid}/prefs/search                         # UserSearchPrefs (sources, allow_ai_generated)
```

> `UserRecord` is on the `users/{uid}` **parent doc** (not a subcollection) so a
> collection stream sees it; quota math is pure in `cookbot/models/quota.py`
> (0 = unlimited).

## WebSocket message pattern

Always use the typed send helpers, never `ws.send_json(raw_dict)` (full catalogue
in `cookbot/protocols/ws_messages.py`):

```python
await ws_send_token(websocket, content="Let me check...")
await ws_send_recipe_options(websocket, proposals)                    # 4-6 cards
await ws_send_final_recipe(websocket, recipe, RecipeSource.WEB_SEARCH)
await ws_send_hitl_checkpoint(websocket, checkpoint, ui.hitl)         # labels from TenantConfig.ui
await ws_send_error(websocket, message="Something went wrong.")
```

In the chat flow, tools never call these directly — they append `TurnEvent`s to
`deps.events` and the WS handler's `_emit_event` maps each event to its helper.
See [cookbot-core agents/CLAUDE.md](../../../packages/cookbot-core/cookbot/agents/CLAUDE.md).

## Grocery matching (`api/grocery.py`)

`POST /v1/grocery/{shop}/match` is the reference consumer of the `delivery-shops`
package, and the one place both matching capabilities are wired together:

- **Search-first, feed-fallback.** It feature-detects with `supports_search(shop)`
  and only builds a `ProductMatcher` from the 50 MB feed when the live search path
  fails wholesale. Keep the fallback — it is what makes a third-party API outage a
  slow request instead of a broken feature.
- **The LLM re-ranker is opt-in on the search path** (`TenantConfig.grocery_llm_rerank`,
  default `False`): Frisco's own ranking already scores 10/10 live, so re-deciding
  it would spend STEP 42 quota per match. It still runs unconditionally on the feed
  fallback, where lexical shortlists genuinely are ambiguous.
- The route carries **no user identity** (it is a stateless computation over an
  ingredient list, reachable with the widget's API key alone) — which is why
  `require_password_set` is deliberately not applied to it or to `shopping_list`.
  `shopping_list` is the narrower case: identity-*aware*, never identity-*required*
  (see below).

## Pantry-aware shopping list (`api/shopping_list.py`, STEP 51)

Two **independent** user-facing flags, and conflating them is the mistake to avoid:

| Flag | Where it travels | What it does |
|---|---|---|
| `use_spizarnia` | connect-time WS **query param** | appends a `[Pantry: …]` hint to each turn, biasing which recipes get proposed |
| `subtract_pantry` | **per-turn** WS payload + REST body | deducts the pantry from a generated shopping list |

- **The new flag is per-turn on purpose.** `use_spizarnia` is read once at the
  handshake, so toggling it mid-session does nothing without a reconnect — a trap
  this feature deliberately does not repeat.
- **The pantry is loaded for every authenticated connection**, not just when
  `use_spizarnia` is set, because the per-turn flag isn't known at handshake time.
  The proposal hint therefore stays explicitly gated on `use_spizarnia` — reducing
  that check to `if spizarnia_items` would bias every turn with an unchecked box.
- **Subtraction is deterministic Python, never the LLM**: `cookbot/models/pantry_math.py`
  runs *after* the ShoppingListAgent, so the agent keeps its single job and the
  feature costs **zero extra tokens**.
- **`POST /v1/shopping-list/build` is identity-aware, not identity-required.** A
  valid token (or the `x-dev-uid` bypass) plus `subtract_pantry=true` reads the
  pantry; anonymous API-key callers behave exactly as before, and an invalid token
  or a failed pantry read degrades to the plain list rather than erroring. The
  anonymous path is the widget's, and it must never break.
- **The pantry is read-only here.** Building a list never mutates it.

Details and the Frisco licensing blocker:
[delivery-shops/CLAUDE.md](../../../packages/delivery-shops/CLAUDE.md).

## Auth

Two middleware layers: an **API key** (`x-api-key`, the key embedded in the widget
script tag) gates session creation; a **Firebase ID token** (`Authorization:
Bearer …`) carries user identity on REST + WS. Dev bypass: `x-dev-uid` header when
`DEV_UID` is set — never in prod. Login is gated by `ALLOWED_EMAILS` (checked after
token verify on both REST and WS; empty = open).

`ALLOWED_EMAILS` is a **bootstrap** list: when it rejects an email,
`get_current_user` falls back to `firestore.find_user_record(uid)` and an existing,
non-disabled record authorizes the caller — so admin-created accounts (STEP 44)
work without a redeploy. `find_user_record` is read-only on purpose;
`get_user_record` *creates* a default and would make the whitelist a no-op.

Admin-created accounts get a server-generated temp password
(`cookbot/models/password.py`, `generate_temp_password()`: `secrets.choice` over an
unambiguous alphabet with no `0/O/1/l/I`, 12 chars, ≥1 digit). It is kept pure and
out of the API module so it stays unit-testable, and it is shown **once** in the
admin panel — there is no email sender, and the response is never refetched.
Forced rotation is `UserRecord.must_change_password` (Firebase has no such concept,
so the server owns it); `POST /v1/me/password` updates Firebase via
`asyncio.to_thread` (blocking SDK — Architecture Rule 4) and then clears the flag.

Dependency chain: `get_current_user` → `get_user_record` → `require_password_set`
(423 while `must_change_password`) / `require_admin` (403 unless role=='admin').
`POST /v1/me/password` deliberately depends on `get_user_record`, **not**
`require_password_set` — it is the one route a locked user may call. The entry
points that resolve identity themselves (`POST /v1/sessions`, the WS handshake)
call `record_is_locked(firestore, uid)` instead of using the dependency chain.

## Adding a new client

1. Copy `clients/tastyhub/` → `clients/{new_client}/`.
2. Update `app/config/tenant.py` with the client-specific `TenantConfig`.
3. Update `Dockerfile` and `cloudbuild.yaml` with the new service name.
4. Deploy: `gcloud run deploy cookbot-{new_client} …` (see [DEPLOY.md](../../../DEPLOY.md)).

`cookbot-core` requires **zero changes** to add a client.
