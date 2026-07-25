from urllib.parse import unquote

from cookbot.models.user import RecipeSource, UserProfile, UserSearchPrefs
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.middleware.auth import get_current_user, require_password_set

# Gated on the caller having replaced a temp password — see middleware/auth.py.
router = APIRouter(dependencies=[Depends(require_password_set)])


class UpdateSearchPrefsRequest(BaseModel):
    search_mode: str | None = None
    sources: list[RecipeSource] | None = None
    allow_ai_generated: bool | None = None


class AddSourceRequest(BaseModel):
    url: str
    name: str


@router.get("/search-prefs", response_model=UserSearchPrefs)
async def get_search_prefs(
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> UserSearchPrefs:
    return await request.app.state.firestore.get_search_prefs(user.uid)


@router.put("/search-prefs", response_model=UserSearchPrefs)
async def update_search_prefs(
    body: UpdateSearchPrefsRequest,
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> UserSearchPrefs:
    prefs = await request.app.state.firestore.get_search_prefs(user.uid)
    if body.search_mode is not None:
        if body.search_mode not in ("sites_only", "sites_and_internet", "internet_only"):
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid search_mode")
        prefs.search_mode = body.search_mode
    if body.sources is not None:
        prefs.sources = body.sources
    if body.allow_ai_generated is not None:
        prefs.allow_ai_generated = body.allow_ai_generated
    await request.app.state.firestore.save_search_prefs(prefs)
    return prefs


@router.post("/search-prefs/sources", response_model=UserSearchPrefs)
async def add_source(
    body: AddSourceRequest,
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> UserSearchPrefs:
    prefs = await request.app.state.firestore.get_search_prefs(user.uid)
    if not any(s.url == body.url for s in prefs.sources):
        prefs.sources.append(RecipeSource(url=body.url, name=body.name))
        await request.app.state.firestore.save_search_prefs(prefs)
    return prefs


@router.delete("/search-prefs/sources/{url_encoded}", response_model=UserSearchPrefs)
async def remove_source(
    url_encoded: str,
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> UserSearchPrefs:
    url = unquote(url_encoded)
    prefs = await request.app.state.firestore.get_search_prefs(user.uid)
    prefs.sources = [s for s in prefs.sources if s.url != url]
    await request.app.state.firestore.save_search_prefs(prefs)
    return prefs
