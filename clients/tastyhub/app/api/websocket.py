from datetime import UTC, datetime

import structlog
from cookbot.agents.chat import (
    CalendarAddEvent,
    CalendarRemoveEvent,
    ChatAgentDeps,
    FinalRecipeEvent,
    RecipeOptionsEvent,
    ShoppingListEvent,
    TurnEvent,
    build_chat_agent,
    dump_chat_state,
    restore_chat_state,
    stream_chat_response,
)
from cookbot.hitl.persistence import restore_checkpoint
from cookbot.models.calendar import CalendarState
from cookbot.models.quota import (
    BudgetStatus,
    check_budget,
    counter_for,
    day_key,
    month_key,
    next_reset,
)
from cookbot.models.recipe import RecipeSource
from cookbot.models.spizarnia import SpizarniaItem
from cookbot.models.tenant import TenantConfig
from cookbot.models.user import DEFAULT_SOURCES, UserSearchPrefs
from cookbot.protocols.ws_messages import (
    WsInbound,
    WsMessageType,
    ws_send_calendar_add,
    ws_send_calendar_remove,
    ws_send_error,
    ws_send_final_recipe,
    ws_send_hitl_checkpoint,
    ws_send_quota_exceeded,
    ws_send_recipe_options,
    ws_send_shopping_list_update,
    ws_send_token,
)
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect
from pydantic_ai.messages import ModelRequest, UserPromptPart

from app.auth_policy import email_allowed
from app.config.settings import get_settings

log = structlog.get_logger()
router = APIRouter()


async def _emit_event(websocket: WebSocket, ev: TurnEvent) -> None:
    """Translate one ordered TurnEvent into its WebSocket message."""
    match ev:
        case FinalRecipeEvent():
            source_enum = (
                RecipeSource.WEB_SEARCH
                if ev.source == "web_search"
                else RecipeSource.AI_GENERATED
            )
            await ws_send_final_recipe(websocket, ev.recipe, source_enum)
        case RecipeOptionsEvent():
            await ws_send_recipe_options(websocket, ev.proposals)
        case CalendarAddEvent():
            await ws_send_calendar_add(websocket, ev.entry)
        case CalendarRemoveEvent():
            await ws_send_calendar_remove(websocket, ev.entry_id)
        case ShoppingListEvent():
            flat = [i.name for i in ev.shopping_list.items]
            await ws_send_shopping_list_update(
                websocket, flat, replace=True, structured=ev.shopping_list
            )


async def _check_quota(firestore, uid: str, config: TenantConfig) -> tuple[bool, BudgetStatus | None, bool]:
    """Load the user's record + current-window counters and decide if the next
    turn is allowed. Returns (allowed, status, disabled). A disabled account is
    always refused. Called before running a turn (never mid-stream)."""
    now = datetime.now(UTC)
    tz = config.quota_timezone
    dk, mk = day_key(now, tz), month_key(now, tz)
    rec = await firestore.get_user_record(
        uid,
        default_quota=config.default_quota(),
        admin_uids=frozenset(config.admin_uids),
    )
    if rec.disabled:
        return False, None, True
    daily = counter_for(await firestore.get_usage_counter(uid, dk), dk)
    monthly = counter_for(await firestore.get_usage_counter(uid, mk), mk)
    return (status := check_budget(rec.quota, daily, monthly)).allowed, status, False


async def _record_usage(firestore, uid: str, config: TenantConfig, tokens: int) -> None:
    """Add this turn's token spend to the user's day + month counters."""
    if tokens <= 0:
        return
    now = datetime.now(UTC)
    tz = config.quota_timezone
    await firestore.add_usage(uid, [day_key(now, tz), month_key(now, tz)], tokens)


def _is_user_turn(msg: object) -> bool:
    """True if msg is a ModelRequest that contains a real user prompt (not just
    tool-return parts). Cutting history here can never orphan a tool message."""
    if not isinstance(msg, ModelRequest):
        return False
    return any(isinstance(p, UserPromptPart) for p in msg.parts)


def _safe_history_cut(history: list, keep_at_least: int) -> int:
    """Index of the earliest user turn at or after (len - keep_at_least).

    Returns 0 (no trim) if no safe cut point exists, so we never slice the
    history in a way that leaves a tool-return without its tool_calls message.
    """
    start = len(history) - keep_at_least
    for i in range(start, len(history)):
        if _is_user_turn(history[i]):
            return i
    return 0


async def _receive_inbound(websocket: WebSocket) -> WsInbound:
    raw = await websocket.receive_text()
    try:
        return WsInbound.model_validate_json(raw)
    except Exception:
        return WsInbound(type=WsMessageType.MESSAGE, content=raw.strip())


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(
    websocket: WebSocket,
    session_id: str,
    use_spizarnia: bool = Query(default=False),
    dev_uid: str = Query(default=""),
    token: str = Query(default=""),
) -> None:
    firestore = websocket.app.state.firestore

    session = await firestore.get_session(session_id)
    if session is None:
        await websocket.close(code=4004)
        return

    if session.expires_at < datetime.now(UTC):
        await websocket.close(code=4003)
        return

    # Resolve uid. Auth sources, in order:
    #   1. DEV_UID bypass (x-dev-uid header or dev_uid query param) — local dev.
    #   2. Firebase ID token — from the `authorization: Bearer` header OR the
    #      `token` query param. Browsers can't set headers on a WebSocket, so the
    #      production frontend passes the token as a query param.
    uid: str | None = None
    settings = get_settings()
    auth_header = websocket.headers.get("authorization", "")
    header_token = auth_header.removeprefix("Bearer ").strip() if auth_header.startswith("Bearer ") else ""
    id_token = header_token or token.strip()
    x_dev_uid = websocket.headers.get("x-dev-uid", "") or dev_uid
    if x_dev_uid and settings.dev_uid and x_dev_uid == settings.dev_uid:
        uid = x_dev_uid
    elif id_token:
        email: str = ""
        try:
            import firebase_admin.auth

            from app.middleware.auth import _get_firebase_app
            _get_firebase_app()
            decoded = firebase_admin.auth.verify_id_token(id_token)
            uid = decoded["uid"]
            email = decoded.get("email", "")
        except Exception as exc:
            # Invalid/expired token → stay unauthenticated (uid=None), but don't
            # swallow silently: log so auth failures are diagnosable.
            log.warning("ws_token_verify_failed", error=str(exc))
        # Access whitelist (empty ⇒ open) — a valid token for a non-allowed email
        # is refused before the connection is accepted. Only enforced when the
        # token verified (uid is set); a failed verify already left uid=None.
        # ALLOWED_EMAILS is a *bootstrap* list (STEP 44): an admin-created account
        # is authorized by its existing, non-disabled Firestore UserRecord instead.
        if uid is not None and not email_allowed(email, settings.allowed_emails):
            from app.middleware.auth import record_grants_access

            if not await record_grants_access(firestore, uid):
                log.warning("ws_email_not_allowed", uid=uid)
                await websocket.close(code=4008)
                return

        # A temp-password account must set its own password before chatting —
        # the same 423 gate the REST product routes apply (STEP 44). Reuse the
        # existing error+close path; no new WsMessageType.
        if uid is not None:
            from app.middleware.auth import record_is_locked

            if await record_is_locked(firestore, uid):
                log.warning("ws_password_change_required", uid=uid)
                await websocket.close(code=4009)
                return

    if session.uid is not None and uid != session.uid:
        await websocket.close(code=4001)
        return

    await websocket.accept()

    config = _get_tenant_config()
    ui = config.ui

    # Load spiżarnia items if toggle is on
    spizarnia_items: list[SpizarniaItem] = []
    if use_spizarnia and uid is not None:
        spizarnia = await firestore.get_spizarnia(uid)
        spizarnia_items = spizarnia.items

    # Load search prefs — use user's saved prefs if authenticated, else fall back to defaults
    if uid is not None:
        prefs = await firestore.get_search_prefs(uid)
    else:
        prefs = UserSearchPrefs(uid="", sources=list(DEFAULT_SOURCES))
    search_site_filter = prefs.site_filter()
    preferred_sites = prefs.preferred_sites()
    allow_ai_generated = prefs.allow_ai_generated

    try:
        # Re-send pending HITL checkpoint on reconnect
        pending = await restore_checkpoint(session_id, firestore)
        if pending is not None:
            await ws_send_hitl_checkpoint(websocket, pending, ui.hitl)

        # Send greeting (and spiżarnia announcement if applicable)
        await ws_send_token(websocket, content=ui.greeting)
        if use_spizarnia and spizarnia_items:
            items_str = ", ".join(i.name for i in spizarnia_items)
            await ws_send_token(websocket, content=f"Używam składników z Twojej spiżarni: {items_str}.")

        # ── Connection-scoped state ───────────────────────────────────────
        # These live for the entire WebSocket connection lifetime.
        # deps.onboarding accumulates across turns; message_history grows each turn.
        agent = build_chat_agent(config)
        deps = ChatAgentDeps(
            config=config,
            search_site_filter=search_site_filter,
            preferred_sites=preferred_sites,
            allow_ai_generated=allow_ai_generated,
        )

        # Resume a previous conversation if one was persisted (Cloud Run
        # containers are stateless — a reconnect may land on a fresh instance).
        message_history: list = []
        raw_state = await firestore.get_chat_state(session_id)
        if raw_state is not None:
            try:
                message_history = restore_chat_state(raw_state, deps)
                log.info("chat_state_restored", session_id=session_id,
                         messages=len(message_history))
            except Exception as exc:
                # A corrupt/stale snapshot must never block the chat — start fresh.
                log.warning("chat_state_restore_failed",
                            session_id=session_id, error=str(exc))

        spiz_suffix = (
            f"\n[Pantry: {', '.join(i.name for i in spizarnia_items)}]"
            if spizarnia_items else ""
        )

        # ── Main chat loop ────────────────────────────────────────────────
        while True:
            msg = await _receive_inbound(websocket)

            if msg.type in (WsMessageType.HITL_RESPONSE, WsMessageType.SPIZARNIA_RESPONSE):
                continue

            user_text = (msg.content or "").strip()
            if not user_text:
                continue

            # Per-turn input: refresh calendar from this message (frontend sends current state)
            deps.calendar = msg.calendar or CalendarState()

            # Clear per-turn output collectors (contract lives in reset_turn)
            deps.reset_turn()

            # Per-user token quota (STEP 42). Refuse the next turn once a budget
            # is exhausted; a turn already in flight is not interrupted. Only
            # meter authenticated users — an unauthenticated fallback session has
            # no record to meter against.
            if uid is not None:
                allowed, status, disabled = await _check_quota(firestore, uid, config)
                if disabled:
                    await ws_send_error(websocket, message=ui.quota_disabled)
                    continue
                if not allowed and status is not None:
                    window = status.exceeded_window or "daily"
                    resets = next_reset(datetime.now(UTC), config.quota_timezone, window)
                    template = (
                        ui.quota_monthly_reached if window == "monthly"
                        else ui.quota_daily_reached
                    )
                    resets_local = resets.strftime("%Y-%m-%d %H:%M")
                    await ws_send_quota_exceeded(
                        websocket,
                        window=window,
                        message=template.format(resets=resets_local),
                        resets_at=resets.isoformat(),
                    )
                    log.info("ws_quota_exceeded", session_id=session_id, uid=uid,
                             window=window)
                    continue

            # Keep history bounded — drop oldest messages but only ever cut at a
            # real user turn, never mid-tool-call. A tool-return is also a
            # ModelRequest, so we must look for a UserPromptPart, not just kind.
            if len(message_history) > 10:
                cut = _safe_history_cut(message_history, keep_at_least=10)
                if cut > 0:
                    message_history[:] = message_history[cut:]

            # Stream agent response — message_history is updated inside the block.
            # A failed turn (LLM error, usage limit hit, …) must not kill the
            # connection: report it and wait for the next message.
            try:
                async with stream_chat_response(
                    agent, deps, message_history, user_text + spiz_suffix
                ) as tokens:
                    async for token in tokens:
                        await ws_send_token(websocket, content=token)
            except WebSocketDisconnect:
                raise
            except Exception as exc:
                log.exception("ws_turn_error", session_id=session_id, error=str(exc))
                await ws_send_error(websocket, message="Something went wrong. Please try again.")
                continue

            # Meter this turn's token spend against the user's quota (best-effort
            # — a Firestore hiccup must not break the live chat; the per-turn
            # UsageLimits already capped the turn itself).
            if uid is not None and deps.last_turn_total_tokens > 0:
                try:
                    await _record_usage(firestore, uid, config, deps.last_turn_total_tokens)
                except Exception as exc:
                    log.warning("ws_usage_record_failed", session_id=session_id, error=str(exc))

            # Drain the ordered side-effect events the tools appended this turn.
            log.info("ws_turn_end", event_count=len(deps.events),
                     events=[ev.kind for ev in deps.events])
            for ev in deps.events:
                await _emit_event(websocket, ev)

            # Persist the resumable conversation snapshot (best-effort — a
            # Firestore hiccup must not break the live chat).
            try:
                await firestore.save_chat_state(
                    session_id, dump_chat_state(deps, message_history)
                )
            except Exception as exc:
                log.warning("chat_state_save_failed",
                            session_id=session_id, error=str(exc))

    except WebSocketDisconnect:
        log.info("ws_disconnect", session_id=session_id)
    except Exception as exc:
        log.exception("ws_error", session_id=session_id, error=str(exc))
        try:
            await ws_send_error(websocket, message="Something went wrong. Please try again.")
        except Exception:
            pass


def _get_tenant_config():
    from app.config.tenant import TASTYHUB_CONFIG
    return TASTYHUB_CONFIG
