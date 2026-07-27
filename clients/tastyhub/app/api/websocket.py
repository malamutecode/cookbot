from datetime import UTC, datetime

import structlog
from cookbot.agents.chat import (
    CalendarAddEvent,
    CalendarRemoveEvent,
    ChatAgentDeps,
    FinalRecipeEvent,
    ProgressEvent,
    RecipeOptionsEvent,
    ShoppingListEvent,
    TurnEvent,
    build_chat_agent,
    dump_chat_state,
    pick_proposal,
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
    ws_send_agent_update,
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


async def _persist_calendar(coro, uid: str) -> None:
    """Await a calendar write, swallowing failures.

    A Firestore hiccup must never cost the user the WS message (and with it the
    entry in the browser) — the turn continues and the loss is one persisted
    write, recoverable by re-adding.
    """
    try:
        await coro
    except Exception as exc:
        log.warning("ws_calendar_persist_failed", uid=uid, error=str(exc))


async def _drain_progress(websocket: WebSocket, deps) -> None:
    """Send any progress notes tools have emitted since the last drain.

    Progress is the ONE event kind sent mid-turn. Everything else describes a
    completed side-effect whose order relative to the final answer matters (a
    recipe card, a calendar write), so those stay batched until the turn ends.

    Best-effort: a failed status line must never take down a turn that is
    otherwise working — it is decoration over a spinner, not content.
    """
    try:
        for message in deps.drain_progress():
            await ws_send_agent_update(websocket, agent="", status=message)
    except WebSocketDisconnect:
        raise
    except Exception as exc:
        log.warning("ws_progress_send_failed", error=str(exc))


async def _emit_event(
    websocket: WebSocket,
    ev: TurnEvent,
    firestore=None,
    uid: str | None = None,
    calendar: CalendarState | None = None,
) -> None:
    """Translate one ordered TurnEvent into its WebSocket message.

    Calendar events are also *persisted* here rather than in the tool that
    emitted them (STEP 52): `add_to_calendar` stays pure and Firestore-free, and
    the handler — which already owns the I/O — performs the side-effect. The
    same arm mutates the in-memory `calendar` so later turns on this connection
    see the change without a re-read. Anonymous (uid-less) connections own no
    document and skip the write entirely.
    """
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
            if firestore is not None and uid is not None:
                await _persist_calendar(firestore.add_calendar_entry(uid, ev.entry), uid)
            if calendar is not None:
                calendar.entries = [e for e in calendar.entries if e.id != ev.entry.id]
                calendar.entries.append(ev.entry)
            await ws_send_calendar_add(websocket, ev.entry)
        case CalendarRemoveEvent():
            if firestore is not None and uid is not None:
                await _persist_calendar(
                    firestore.remove_calendar_entry(uid, ev.entry_id), uid
                )
            if calendar is not None:
                calendar.entries = [e for e in calendar.entries if e.id != ev.entry_id]
            await ws_send_calendar_remove(websocket, ev.entry_id)
        case ShoppingListEvent():
            flat = [i.name for i in ev.shopping_list.items]
            await ws_send_shopping_list_update(
                websocket, flat, replace=True, structured=ev.shopping_list
            )
        case ProgressEvent():
            # Reuses the existing `agent_update` status channel, which the widget
            # already renders — no new message type and no frontend change. The
            # agent field is blank because the frontend formats "{agent}: {status}"
            # and the phase name alone reads better than "chat: Czytam przepis…".
            #
            # Normally drained mid-turn by _drain_progress (see the streaming
            # loop); this arm covers the pick_recipe path, which resolves a card
            # outside run_stream and drains its events only at the end.
            await ws_send_agent_update(websocket, agent="", status=ev.message)


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

    # Load the pantry once per connection. It feeds two INDEPENDENT features:
    #   • the "[Pantry: …]" proposal hint below, gated on the connect-time
    #     `use_spizarnia` param;
    #   • shopping-list subtraction (STEP 51), gated on the per-turn
    #     `subtract_pantry` flag, which is not known yet at handshake time.
    # Hence it is fetched for any authenticated user, not only when use_spizarnia
    # is set — one read per connection either way.
    spizarnia_items: list[SpizarniaItem] = []
    if uid is not None:
        spizarnia = await firestore.get_spizarnia(uid)
        spizarnia_items = spizarnia.items

    # Load the calendar ONCE per connection (STEP 52). The server is the only
    # writer on this path, so `_emit_event` keeps this copy current in memory
    # instead of re-reading per turn. Anonymous connections get an empty plan
    # they can read but never persist.
    calendar = await firestore.get_calendar(uid) if uid is not None else CalendarState()

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
        # `deps.calendar` is the SAME object as the connection-scoped `calendar`,
        # not a copy — that aliasing is what lets `_emit_event`'s in-memory
        # mutation be visible to the next turn's tools.
        deps = ChatAgentDeps(
            config=config,
            search_site_filter=search_site_filter,
            preferred_sites=preferred_sites,
            allow_ai_generated=allow_ai_generated,
            calendar=calendar,
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

        # The proposal hint stays gated on use_spizarnia — the pantry is now loaded
        # unconditionally (for STEP 51 subtraction), so this check can NOT be
        # reduced to "if spizarnia_items" or an unchecked box would still bias
        # every turn's proposals.
        spiz_suffix = (
            f"\n[Pantry: {', '.join(i.name for i in spizarnia_items)}]"
            if use_spizarnia and spizarnia_items else ""
        )

        # ── Main chat loop ────────────────────────────────────────────────
        while True:
            msg = await _receive_inbound(websocket)

            if msg.type in (WsMessageType.HITL_RESPONSE, WsMessageType.SPIZARNIA_RESPONSE):
                continue

            # ── Structured proposal pick — no LLM in the selection path ──────
            # A card click carries its index as data, so the chosen recipe is
            # resolved directly instead of an LLM re-deriving it from "wybieram
            # 2". That round-trip resolved the wrong card, and add_to_calendar
            # then persisted whatever `last_recipe` held. Falls through to the
            # conversational path when the index is stale (e.g. a reconnect
            # dropped the proposals), which re-asks rather than guessing.
            if msg.type == WsMessageType.PICK_RECIPE and msg.index is not None:
                deps.subtract_pantry = msg.subtract_pantry
                deps.pantry = spizarnia_items
                deps.reset_turn()
                try:
                    found = await pick_proposal(deps, msg.index)
                except WebSocketDisconnect:
                    raise
                except Exception as exc:
                    log.exception("ws_pick_error", session_id=session_id, error=str(exc))
                    await ws_send_error(
                        websocket, message="Something went wrong. Please try again."
                    )
                    continue
                # Only the clean case short-circuits the model. A split question
                # (STEP 45) and an error both need the agent to SPEAK — they
                # emit no FinalRecipeEvent, so returning here would leave the
                # user with a spinner and no message. Those fall through and are
                # handled conversationally, exactly as before.
                if found is not None and not found.split_question and found.source != "error":
                    # Keep the conversation coherent: record the pick as a user
                    # turn so a later "add it to the calendar" has the context.
                    message_history.append(
                        ModelRequest(parts=[UserPromptPart(
                            content=f"[user picked option {msg.index}: {found.recipe.name}]"
                        )])
                    )
                    for ev in deps.events:
                        await _emit_event(websocket, ev, firestore, uid, calendar)
                    try:
                        await firestore.save_chat_state(
                            session_id, dump_chat_state(deps, message_history)
                        )
                    except Exception as exc:
                        log.warning("chat_state_save_failed",
                                    session_id=session_id, error=str(exc))
                    continue
                # Fall through to the conversational path below. Two cases reach
                # here: a stale index (found is None — deps untouched, the model
                # re-asks), and a split/error result (deps already carry the
                # pending split or the untouched proposals, and the model turn
                # asks the question). The client sends `content` alongside the
                # index so that turn has something to run on.
                log.info("ws_pick_fallback", session_id=session_id, index=msg.index,
                         resolved=found is not None)

            user_text = (msg.content or "").strip()
            if not user_text:
                continue

            # Per-turn input: pantry subtraction (STEP 51). Refreshed from THIS
            # message so toggling the checkbox mid-session takes effect on the
            # next turn, without a reconnect.
            deps.subtract_pantry = msg.subtract_pantry
            deps.pantry = spizarnia_items

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
                    # Progress emitted by tools BEFORE the first token — the whole
                    # point of the event is to fill the silent fetch→extract→scale
                    # stretch, and by the time a token arrives that stretch is over.
                    await _drain_progress(websocket, deps)
                    async for token in tokens:
                        await ws_send_token(websocket, content=token)
                        await _drain_progress(websocket, deps)
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
                await _emit_event(websocket, ev, firestore, uid, calendar)

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
