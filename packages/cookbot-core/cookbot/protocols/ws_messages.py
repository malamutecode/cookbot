from enum import Enum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from cookbot.hitl.models import HITLCheckpoint
from cookbot.models.recipe import Recipe, RecipeSource
from cookbot.models.ui_strings import HitlLabels

if TYPE_CHECKING:
    from fastapi import WebSocket


class WsMessageType(str, Enum):
    MESSAGE = "message"
    TOKEN = "token"
    AGENT_UPDATE = "agent_update"
    HITL_CHECKPOINT = "hitl_checkpoint"
    HITL_RESPONSE = "hitl_response"
    FINAL_RECIPE = "final_recipe"
    ERROR = "error"


class WsInbound(BaseModel):
    type: WsMessageType
    content: str | None = None
    approved: bool | None = None
    modification: str | None = None


class WsOutToken(BaseModel):
    type: WsMessageType = WsMessageType.TOKEN
    content: str


class WsOutAgentUpdate(BaseModel):
    type: WsMessageType = WsMessageType.AGENT_UPDATE
    agent: str
    status: str


class WsOutHitlLabels(BaseModel):
    heading: str
    approve: str
    modify: str
    reject: str
    modify_placeholder: str
    modify_send: str
    approved_note: str
    rejected_note: str
    modification_note: str


class WsOutHitlCheckpoint(BaseModel):
    type: WsMessageType = WsMessageType.HITL_CHECKPOINT
    recipe: Recipe
    round: int
    labels: WsOutHitlLabels


class WsOutFinalRecipe(BaseModel):
    type: WsMessageType = WsMessageType.FINAL_RECIPE
    recipe: Recipe
    source: RecipeSource


class WsOutError(BaseModel):
    type: WsMessageType = WsMessageType.ERROR
    message: str


async def ws_send_token(websocket: "WebSocket", content: str) -> None:
    await websocket.send_text(WsOutToken(content=content).model_dump_json())


async def ws_send_agent_update(websocket: "WebSocket", agent: str, status: str) -> None:
    await websocket.send_text(WsOutAgentUpdate(agent=agent, status=status).model_dump_json())


async def ws_send_hitl_checkpoint(
    websocket: "WebSocket", checkpoint: HITLCheckpoint, labels: HitlLabels
) -> None:
    msg = WsOutHitlCheckpoint(
        recipe=checkpoint.recipe,
        round=checkpoint.round_number,
        labels=WsOutHitlLabels(
            heading=labels.heading,
            approve=labels.approve,
            modify=labels.modify,
            reject=labels.reject,
            modify_placeholder=labels.modify_placeholder,
            modify_send=labels.modify_send,
            approved_note=labels.approved_note,
            rejected_note=labels.rejected_note,
            modification_note=labels.modification_note,
        ),
    )
    await websocket.send_text(msg.model_dump_json())


async def ws_send_final_recipe(
    websocket: "WebSocket", recipe: Recipe, source: RecipeSource
) -> None:
    await websocket.send_text(WsOutFinalRecipe(recipe=recipe, source=source).model_dump_json())


async def ws_send_error(websocket: "WebSocket", message: str) -> None:
    await websocket.send_text(WsOutError(message=message).model_dump_json())
