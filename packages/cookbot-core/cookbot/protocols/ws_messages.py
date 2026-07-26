from enum import StrEnum
from typing import TYPE_CHECKING

from pydantic import BaseModel

from cookbot.hitl.models import HITLCheckpoint
from cookbot.models.calendar import CalendarEntry
from cookbot.models.recipe import Recipe, RecipeSource, RecipeSummary
from cookbot.models.shopping import ShoppingList
from cookbot.models.ui_strings import HitlLabels

if TYPE_CHECKING:
    from fastapi import WebSocket


class WsMessageType(StrEnum):
    MESSAGE = "message"
    TOKEN = "token"
    AGENT_UPDATE = "agent_update"
    HITL_CHECKPOINT = "hitl_checkpoint"
    HITL_RESPONSE = "hitl_response"
    FINAL_RECIPE = "final_recipe"
    RECIPE_OPTIONS = "recipe_options"
    SPIZARNIA_OFFER = "spizarnia_offer"
    SPIZARNIA_RESPONSE = "spizarnia_response"
    CALENDAR_UPDATE = "calendar_update"
    SHOPPING_LIST_UPDATE = "shopping_list_update"
    QUOTA_EXCEEDED = "quota_exceeded"
    ERROR = "error"


class WsInbound(BaseModel):
    type: WsMessageType
    content: str | None = None
    approved: bool | None = None
    modification: str | None = None
    add_missing: bool | None = None       # for spizarnia_response
    remove_used: bool | None = None       # for spizarnia_response
    # NOTE (STEP 52): there is deliberately no `calendar` field. The server owns
    # the calendar (Firestore, loaded at the handshake); accepting a client copy
    # would mean trusting unvalidated state on every turn.
    # STEP 51 — deduct the pantry from a generated shopping list. Sent PER TURN,
    # deliberately NOT as a connect-time query param the way `use_spizarnia` is:
    # a mid-session toggle must reach the server without a reconnect. Defaults
    # to False so an older client's payload still parses.
    subtract_pantry: bool = False


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


class WsOutSpizarniaOffer(BaseModel):
    type: WsMessageType = WsMessageType.SPIZARNIA_OFFER
    missing_ingredients: list[str]
    used_from_spizarnia: list[str]


class WsOutCalendarUpdate(BaseModel):
    type: WsMessageType = WsMessageType.CALENDAR_UPDATE
    action: str          # "add" | "remove"
    entry: CalendarEntry | None = None
    entry_id: str | None = None  # used for remove


class WsOutRecipeOptions(BaseModel):
    type: WsMessageType = WsMessageType.RECIPE_OPTIONS
    proposals: list[RecipeSummary]


class WsOutShoppingListUpdate(BaseModel):
    type: WsMessageType = WsMessageType.SHOPPING_LIST_UPDATE
    items: list[str]                  # flat names — always present for backward compat
    replace: bool = False             # if True, replace all; if False, merge
    structured: ShoppingList | None = None  # structured sections when available


class WsOutQuotaExceeded(BaseModel):
    type: WsMessageType = WsMessageType.QUOTA_EXCEEDED
    window: str          # "daily" | "monthly"
    message: str         # user-facing, localized
    resets_at: str       # ISO datetime when the exhausted window resets


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


async def ws_send_spizarnia_offer(
    websocket: "WebSocket",
    missing_ingredients: list[str],
    used_from_spizarnia: list[str],
) -> None:
    await websocket.send_text(
        WsOutSpizarniaOffer(
            missing_ingredients=missing_ingredients,
            used_from_spizarnia=used_from_spizarnia,
        ).model_dump_json()
    )


async def ws_send_calendar_add(websocket: "WebSocket", entry: CalendarEntry) -> None:
    await websocket.send_text(
        WsOutCalendarUpdate(action="add", entry=entry).model_dump_json()
    )


async def ws_send_calendar_remove(websocket: "WebSocket", entry_id: str) -> None:
    await websocket.send_text(
        WsOutCalendarUpdate(action="remove", entry_id=entry_id).model_dump_json()
    )


async def ws_send_shopping_list_update(
    websocket: "WebSocket",
    items: list[str],
    replace: bool = False,
    structured: ShoppingList | None = None,
) -> None:
    await websocket.send_text(
        WsOutShoppingListUpdate(items=items, replace=replace, structured=structured).model_dump_json()
    )


async def ws_send_recipe_options(websocket: "WebSocket", proposals: list[RecipeSummary]) -> None:
    await websocket.send_text(WsOutRecipeOptions(proposals=proposals).model_dump_json())


async def ws_send_quota_exceeded(
    websocket: "WebSocket", window: str, message: str, resets_at: str
) -> None:
    await websocket.send_text(
        WsOutQuotaExceeded(window=window, message=message, resets_at=resets_at).model_dump_json()
    )


async def ws_send_error(websocket: "WebSocket", message: str) -> None:
    await websocket.send_text(WsOutError(message=message).model_dump_json())
