from cookbot.agents.shopping_list import build_shopping_list_agent
from cookbot.models.shopping import ShoppingList
from fastapi import APIRouter, Request
from pydantic import BaseModel

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
