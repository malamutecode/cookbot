import asyncio
from datetime import UTC, datetime

import structlog
from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from cookbot.agents.ingredient import build_ingredient_agent, intent_to_prompt
from cookbot.agents.intake import build_intake_agent
from cookbot.hitl.gate import HITLGate
from cookbot.hitl.models import HITLResponse
from cookbot.hitl.persistence import restore_checkpoint
from cookbot.models.recipe import Recipe, RecipeSource, UserIntent
from cookbot.orchestrator.session import SessionOrchestrator
from cookbot.protocols.ws_messages import (
    WsInbound,
    WsMessageType,
    ws_send_agent_update,
    ws_send_error,
    ws_send_final_recipe,
    ws_send_hitl_checkpoint,
    ws_send_token,
)

log = structlog.get_logger()
router = APIRouter()


async def _receive_text(websocket: WebSocket) -> str:
    raw = await websocket.receive_text()
    try:
        return WsInbound.model_validate_json(raw).content or ""
    except Exception:
        return raw.strip()


async def _receive_inbound(websocket: WebSocket) -> WsInbound:
    raw = await websocket.receive_text()
    try:
        return WsInbound.model_validate_json(raw)
    except Exception:
        return WsInbound(type=WsMessageType.MESSAGE, content=raw.strip())


@router.websocket("/ws/{session_id}")
async def websocket_endpoint(websocket: WebSocket, session_id: str) -> None:
    firestore = websocket.app.state.firestore
    settings = websocket.app.state.settings

    session = await firestore.get_session(session_id)
    if session is None:
        await websocket.close(code=4004)
        return

    if session.expires_at < datetime.now(UTC):
        await websocket.close(code=4003)
        return

    await websocket.accept()

    config = _get_tenant_config()
    ui = config.ui

    try:
        # ── Reconnect: re-send pending HITL checkpoint if one exists ─────
        pending = await restore_checkpoint(session_id, firestore)
        if pending is not None:
            await ws_send_hitl_checkpoint(websocket, pending, ui.hitl)

        await ws_send_token(websocket, content=ui.greeting)

        # ── Onboarding: 5 questions ──────────────────────────────────────
        answers: list[str] = []
        for question in ui.intake_questions:
            await ws_send_token(websocket, content=question)
            answers.append(await _receive_text(websocket))

        await ws_send_token(websocket, content=ui.thinking)

        # ── IntakeAgent: answers → UserIntent ────────────────────────────
        intake_agent = build_intake_agent(config)
        combined = "\n".join(
            f"Q: {q}\nA: {a}" for q, a in zip(ui.intake_questions, answers)
        )
        intake_result = await intake_agent.run(combined)
        intent: UserIntent = intake_result.output

        # ── IngredientAgent: UserIntent → ParsedIngredients ──────────────
        ingredient_agent = build_ingredient_agent(config)
        ing_result = await ingredient_agent.run(intent_to_prompt(intent))
        ingredients = ing_result.output

        time_str = str(intent.max_time_minutes) if intent.max_time_minutes else "—"
        items_str = ", ".join(ingredients.items) or "—"
        await ws_send_token(
            websocket,
            content=ui.summary_prefix.format(
                dish=intent.dish_type, time=time_str, items=items_str
            ),
        )

        # ── Set up HITL gate ─────────────────────────────────────────────
        gate = HITLGate(session_id=session_id, firestore=firestore)

        # ── WS-side HITL driver (concurrent with orchestrator) ───────────
        # Runs as a background task: waits for each checkpoint, sends it to
        # the client, waits for the HITL_RESPONSE message, forwards it to gate.
        hitl_done = asyncio.Event()

        async def hitl_driver() -> None:
            try:
                while not hitl_done.is_set():
                    try:
                        checkpoint = await asyncio.wait_for(gate.get_checkpoint(), timeout=3600.0)
                    except asyncio.TimeoutError:
                        break
                    await ws_send_hitl_checkpoint(websocket, checkpoint, ui.hitl)
                    msg = await _receive_inbound(websocket)
                    approved = msg.approved if msg.approved is not None else False
                    response = HITLResponse(approved=approved, modification=msg.modification)
                    await gate.submit_response(response)
            except WebSocketDisconnect:
                pass
            except Exception as exc:
                log.exception("hitl_driver_error", session_id=session_id, error=str(exc))

        hitl_task = asyncio.create_task(hitl_driver())

        # ── Orchestrator callbacks ───────────────────────────────────────
        async def on_token(content: str) -> None:
            await ws_send_token(websocket, content=content)

        async def on_agent_update(agent: str, status: str) -> None:
            await ws_send_agent_update(websocket, agent=agent, status=status)

        async def on_final_recipe(recipe: Recipe, source: RecipeSource) -> None:
            await ws_send_final_recipe(websocket, recipe=recipe, source=source)

        async def on_error(message: str) -> None:
            await ws_send_error(websocket, message=message)

        # ── Run orchestrator ─────────────────────────────────────────────
        orchestrator = SessionOrchestrator(config, firestore)
        await orchestrator.run(
            session_id=session_id,
            intent=intent,
            ingredients=ingredients,
            gate=gate,
            on_token=on_token,
            on_agent_update=on_agent_update,
            on_final_recipe=on_final_recipe,
            on_error=on_error,
        )

        hitl_done.set()
        hitl_task.cancel()
        try:
            await hitl_task
        except asyncio.CancelledError:
            pass

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
