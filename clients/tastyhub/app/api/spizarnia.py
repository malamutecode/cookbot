from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.middleware.auth import get_current_user
from cookbot.models.spizarnia import Spizarnia, SpizarniaItem
from cookbot.models.user import UserProfile

router = APIRouter()


class AddItemsRequest(BaseModel):
    items: list[SpizarniaItem]


class AddFromRecipeRequest(BaseModel):
    recipe_id: str
    add_missing: bool


@router.get("/spizarnia", response_model=Spizarnia)
async def get_spizarnia(
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> Spizarnia:
    return await request.app.state.firestore.get_spizarnia(user.uid)


@router.post("/spizarnia/items", response_model=Spizarnia)
async def add_items(
    body: AddItemsRequest,
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> Spizarnia:
    await request.app.state.firestore.add_spizarnia_items(user.uid, body.items)
    return await request.app.state.firestore.get_spizarnia(user.uid)


@router.delete("/spizarnia/items/{item_name}", response_model=Spizarnia)
async def remove_item(
    item_name: str,
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> Spizarnia:
    await request.app.state.firestore.remove_spizarnia_item(user.uid, item_name)
    return await request.app.state.firestore.get_spizarnia(user.uid)


@router.post("/spizarnia/add-from-recipe", status_code=status.HTTP_204_NO_CONTENT)
async def add_from_recipe(
    body: AddFromRecipeRequest,
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> None:
    # Placeholder: in Phase 2, resolve recipe_id → missing ingredients and add them
    pass
