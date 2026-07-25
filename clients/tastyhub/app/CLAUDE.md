# clients/tastyhub — client app

> A thin FastAPI app that imports `cookbot-core` and adds TastyHub-specific
> config. Root context: [CLAUDE.md](../../../CLAUDE.md). **Architecture Rule 1:
> nothing here may be imported by `cookbot-core`** — the dependency is one-way.

## Layout

```
app/
├── main.py        # FastAPI entry point — all routers mounted under /v1
├── api/           # sessions, spizarnia, search_prefs, shopping_list, ui, websocket, …
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
await ws_send_recipe_options(websocket, proposals)                    # 4 cards
await ws_send_final_recipe(websocket, recipe, RecipeSource.WEB_SEARCH)
await ws_send_hitl_checkpoint(websocket, checkpoint, ui.hitl)         # labels from TenantConfig.ui
await ws_send_error(websocket, message="Something went wrong.")
```

In the chat flow, tools never call these directly — they append `TurnEvent`s to
`deps.events` and the WS handler's `_emit_event` maps each event to its helper.
See [cookbot-core agents/CLAUDE.md](../../../packages/cookbot-core/cookbot/agents/CLAUDE.md).

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
