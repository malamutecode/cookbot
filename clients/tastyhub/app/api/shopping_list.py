from cookbot.agents.shopping_list import build_shopping_list_agent
from cookbot.models.shopping import ShoppingList
from fastapi import APIRouter, Request
from pydantic import BaseModel

# NOTE (STEP 44): deliberately NOT gated by require_password_set. This route
# carries no user identity at all — it is a stateless formatter over a list of
# ingredients posted in the body, reachable with only the widget's API key.
# Adding a Firebase-record dependency here would break the anonymous/API-key
# path without protecting any user data. A locked account is stopped at
# /v1/sessions and the WS handshake, which is what actually gates the product.
router = APIRouter()


class BuildShoppingListRequest(BaseModel):
    ingredients: list[str]


@router.post("/shopping-list/build", response_model=ShoppingList)
async def build_shopping_list(body: BuildShoppingListRequest, request: Request) -> ShoppingList:
    if not body.ingredients:
        return ShoppingList(items=[], sections=[])
    from app.config.tenant import TASTYHUB_CONFIG
    agent = build_shopping_list_agent(TASTYHUB_CONFIG)
    raw_text = "\n".join(body.ingredients)
    result = await agent.run(raw_text)
    return result.output
