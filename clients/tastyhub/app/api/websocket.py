import structlog
from datetime import UTC, datetime
from fastapi import APIRouter, Query, WebSocket, WebSocketDisconnect

from app.config.settings import get_settings
from cookbot.agents.chat import ChatAgentDeps, OnboardingState, build_chat_agent, stream_chat_response
from cookbot.hitl.persistence import restore_checkpoint
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
        except Exception:
            pass

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
            allow_ai_generated=allow_ai_generated,
        )
        message_history: list = []

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

            # Refresh calendar from this message (frontend sends current state)
            deps.calendar = msg.calendar or CalendarState()

            # Reset per-turn side-effect collectors
            deps.calendar_adds = []
            deps.calendar_removes = []
            deps.shopping_list_items = None
            deps.recipe_options = []
            deps.recipe_ready_this_turn = False

            # Keep history bounded — drop oldest user/assistant pairs but never cut
            # mid-tool-call (a tool result must always follow its tool_calls message).
            if len(message_history) > 10:
                # Find the first index where we can safely start: a ModelRequest (user turn).
                # ModelRequest has kind="request", ModelResponse has kind="response".
                cut = len(message_history) - 10
                while cut < len(message_history) and getattr(message_history[cut], 'kind', None) != 'request':
                    cut += 1
                message_history[:] = message_history[cut:]

            # Stream agent response — message_history is updated inside the block
            async with stream_chat_response(
                agent, deps, message_history, user_text + spiz_suffix
            ) as tokens:
                async for token in tokens:
                    await ws_send_token(websocket, content=token)

            # Emit side-effects
            log.info("ws_turn_end",
                recipe_ready=deps.recipe_ready_this_turn,
                last_recipe_source=deps.last_recipe.source if deps.last_recipe else None,
                recipe_options_count=len(deps.recipe_options),
                calendar_adds=len(deps.calendar_adds),
            )
            # final_recipe first — recipe card must appear before any new proposals
            if deps.recipe_ready_this_turn and deps.last_recipe is not None and deps.last_recipe.source != "not_found":
                source_enum = (
                    RecipeSource.WEB_SEARCH
                    if deps.last_recipe.source == "web_search"
                    else RecipeSource.AI_GENERATED
                )
                await ws_send_final_recipe(websocket, deps.last_recipe.recipe, source_enum)
            if deps.recipe_options:
                await ws_send_recipe_options(websocket, deps.recipe_options)
            seen_cal_ids: set[str] = set()
            for entry in deps.calendar_adds:
                if entry.id not in seen_cal_ids:
                    seen_cal_ids.add(entry.id)
                    await ws_send_calendar_add(websocket, entry)
            for entry_id in deps.calendar_removes:
                await ws_send_calendar_remove(websocket, entry_id)
            if deps.shopping_list_items is not None:
                sl = deps.shopping_list_items
                flat = [i.name for i in sl.items]
                await ws_send_shopping_list_update(websocket, flat, replace=True, structured=sl)

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
