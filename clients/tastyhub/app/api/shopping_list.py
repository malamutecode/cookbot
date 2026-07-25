import structlog
from cookbot.agents.shopping_list import build_shopping_list_agent
from cookbot.models.pantry_math import subtract_pantry
from cookbot.models.shopping import ShoppingList
from cookbot.models.spizarnia import SpizarniaItem
from fastapi import APIRouter, Header, Request
from pydantic import BaseModel

log = structlog.get_logger()

# NOTE (STEP 44 → amended by STEP 51): this route is identity-*aware*, never
# identity-*required*, and is deliberately NOT gated by require_password_set.
# Its core job is a stateless formatting of ingredients posted in the body, and
# that path must keep working with only the widget's API key — a hard Firebase
# dependency here would break the anonymous path without protecting any user
# data. A locked account is stopped at /v1/sessions and the WS handshake, which
# is what actually gates the product.
#
# STEP 51 added ONE optional identity use: with a valid Bearer token AND
# subtract_pantry=true, the caller's pantry is deducted from the result. A
# missing or invalid token simply skips subtraction — it is never an error, so
# an anonymous caller behaves exactly as before.
router = APIRouter()


class BuildShoppingListRequest(BaseModel):
    ingredients: list[str]
    # Opt-in pantry subtraction. Ignored unless the request also carries a
    # verifiable identity; defaults False so existing clients are unaffected.
    subtract_pantry: bool = False


async def _optional_uid(authorization: str | None, x_dev_uid: str | None) -> str | None:
    """Resolve a uid if the caller supplied one, else None. Never raises.

    Unlike `get_current_user`, a missing/invalid credential is not a 401 here —
    this route's contract is that identity is optional (see the note above).
    """
    from app.config.settings import get_settings

    settings = get_settings()
    if x_dev_uid and settings.dev_uid and x_dev_uid == settings.dev_uid:
        return x_dev_uid
    if not authorization or not authorization.startswith("Bearer "):
        return None
    token = authorization.removeprefix("Bearer ").strip()
    try:
        import asyncio

        import firebase_admin.auth

        from app.middleware.auth import _get_firebase_app

        _get_firebase_app()
        # Blocking SDK call — Architecture Rule 4.
        decoded = await asyncio.to_thread(firebase_admin.auth.verify_id_token, token)
        return decoded.get("uid")
    except Exception as exc:  # noqa: BLE001 — an unverifiable token means "anonymous"
        log.warning("shopping_list_token_verify_failed", error=str(exc))
        return None


@router.post("/shopping-list/build", response_model=ShoppingList)
async def build_shopping_list(
    body: BuildShoppingListRequest,
    request: Request,
    authorization: str | None = Header(default=None),
    x_dev_uid: str | None = Header(default=None),
) -> ShoppingList:
    if not body.ingredients:
        return ShoppingList(items=[], sections=[])
    from app.config.tenant import TASTYHUB_CONFIG
    agent = build_shopping_list_agent(TASTYHUB_CONFIG)
    raw_text = "\n".join(body.ingredients)
    result = await agent.run(raw_text)
    shopping_list: ShoppingList = result.output

    if not body.subtract_pantry:
        return shopping_list

    uid = await _optional_uid(authorization, x_dev_uid)
    if uid is None:
        return shopping_list

    pantry: list[SpizarniaItem] = []
    try:
        pantry = (await request.app.state.firestore.get_spizarnia(uid)).items
    except Exception as exc:  # noqa: BLE001 — a pantry read failure must not lose the list
        log.warning("shopping_list_pantry_load_failed", uid=uid, error=str(exc))
        return shopping_list
    if not pantry:
        return shopping_list

    ui = TASTYHUB_CONFIG.ui
    aware = subtract_pantry(
        shopping_list,
        pantry,
        note_have=ui.pantry_note_have,
        note_check=ui.pantry_note_check,
    )
    return aware.shopping_list
