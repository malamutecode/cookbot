
from cookbot.models.calendar import CalendarState
from cookbot.models.user import UserProfile
from fastapi import APIRouter, Depends, Request

from app.middleware.auth import get_current_user, require_password_set

# Same gate as the pantry: every calendar route needs a caller who has replaced a
# temp password (423 while must_change_password) — see middleware/auth.py.
router = APIRouter(dependencies=[Depends(require_password_set)])


@router.get("/calendar", response_model=CalendarState)
async def get_calendar(
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> CalendarState:
    return await request.app.state.firestore.get_calendar(user.uid)


@router.put("/calendar", response_model=CalendarState)
async def save_calendar(
    body: CalendarState,
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> CalendarState:
    """Whole-state save — the shape drag/drop and slot moves need.

    `uid` is taken from the verified token, never from the body, so a client
    cannot write into someone else's plan by editing the payload.
    """
    state = body.model_copy(update={"uid": user.uid})
    await request.app.state.firestore.save_calendar(state)
    return await request.app.state.firestore.get_calendar(user.uid)


@router.delete("/calendar/entries/{entry_id}", response_model=CalendarState)
async def remove_entry(
    entry_id: str,
    request: Request,
    user: UserProfile = Depends(get_current_user),
) -> CalendarState:
    await request.app.state.firestore.remove_calendar_entry(user.uid, entry_id)
    return await request.app.state.firestore.get_calendar(user.uid)
