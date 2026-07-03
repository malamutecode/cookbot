import structlog
from datetime import UTC, datetime
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.config.settings import get_settings
from cookbot.agents.chat import (
    CalendarAddEvent,
    CalendarRemoveEvent,
    ChatAgentDeps,
    FinalRecipeEvent,
    OnboardingState,
    RecipeOptionsEvent,
    ShoppingListEvent,
    TurnEvent,
    build_chat_agent,
    dump_chat_state,
    restore_chat_state,
    stream_chat_response,
)
from cookbot.hitl.persistence import restore_checkpoint
from pydantic_ai.messages import ModelRequest, UserPromptPart
from cookbot.models.calendar import CalendarState
from cookbot.models.spizarnia import SpizarniaItem
from cookbot.models.recipe import RecipeSource
from cookbot.models.user import DEFAULT_SOURCES, UserSearchPrefs
from cookbot.protocols.ws_messages import (
    WsInbound,
    WsMessageType,
    ws_send_calendar_add,
    ws_send_calendar_remove,
    ws_send_error,
    ws_send_final_recipe,
    ws_send_hitl_checkpoint,
    ws_send_recipe_options,
    ws_send_shopping_list_update,
    ws_send_token,
)

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
) -> None:
    firestore = websocket.app.state.firestore

    session = await firestore.get_session(session_id)
    if session is None:
        await websocket.close(code=4004)
        return

    if session.expires_at < datetime.now(UTC):
        await websocket.close(code=4003)
        return

    # Resolve uid — try Bearer token first, then DEV_UID bypass (header or query param)
    uid: str | None = None
    settings = get_settings()
    auth_header = websocket.headers.get("authorization", "")
    x_dev_uid = websocket.headers.get("x-dev-uid", "") or dev_uid
    if x_dev_uid and settings.dev_uid and x_dev_uid == settings.dev_uid:
        uid = x_dev_uid
    elif auth_header.startswith("Bearer "):
        token = auth_header.removeprefix("Bearer ").strip()
        try:
            import firebase_admin.auth
            from app.middleware.auth import _get_firebase_app
            _get_firebase_app()
            decoded = firebase_admin.auth.verify_id_token(token)
            uid = decoded["uid"]
        except Exception as exc:
            # Invalid/expired token → stay unauthenticated (uid=None), but don't
            # swallow silently: log so auth failures are diagnosable.
            log.warning("ws_token_verify_failed", error=str(exc))

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
