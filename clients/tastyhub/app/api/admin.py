"""Admin API — user management + per-user token quotas (STEP 42).

All routes require the caller's own record to be an admin (require_admin). The
first admin is bootstrapped via ADMIN_UIDS (see settings) so there's a way in
before any admin exists.
"""

from datetime import UTC, datetime

from cookbot.models.quota import counter_for, day_key, month_key
from cookbot.models.user import TokenQuota, UserRecord
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel

from app.config.tenant import TASTYHUB_CONFIG
from app.middleware.auth import get_user_record, require_admin

router = APIRouter()


class UserUsageView(BaseModel):
    """One row in the admin user table: the record plus current-window usage."""

    record: UserRecord
    daily_used: int
    monthly_used: int


class SetQuotaRequest(BaseModel):
    daily_limit: int    # 0 ⇒ unlimited
    monthly_limit: int  # 0 ⇒ unlimited


class SetRoleRequest(BaseModel):
    role: str  # "user" | "admin"


class SetDisabledRequest(BaseModel):
    disabled: bool


class MeView(BaseModel):
    """Minimal self-view so the SPA can decide whether to show the Admin tab."""

    uid: str
    email: str | None
    role: str
    is_admin: bool


@router.get("/me", response_model=MeView)
async def get_me(record: UserRecord = Depends(get_user_record)) -> MeView:
    return MeView(
        uid=record.uid,
        email=record.email,
        role=record.role,
        is_admin=record.is_admin,
    )


async def _usage_view(request: Request, record: UserRecord) -> UserUsageView:
    now = datetime.now(UTC)
    tz = TASTYHUB_CONFIG.quota_timezone
    dk, mk = day_key(now, tz), month_key(now, tz)
    firestore = request.app.state.firestore
    daily = counter_for(await firestore.get_usage_counter(record.uid, dk), dk)
    monthly = counter_for(await firestore.get_usage_counter(record.uid, mk), mk)
    return UserUsageView(
        record=record,
        daily_used=daily.tokens_used,
        monthly_used=monthly.tokens_used,
    )


@router.get("/admin/users", response_model=list[UserUsageView])
async def list_users(
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> list[UserUsageView]:
    records = await request.app.state.firestore.list_user_records()
    return [await _usage_view(request, rec) for rec in records]


@router.put("/admin/users/{uid}/quota", response_model=UserRecord)
async def set_quota(
    uid: str,
    body: SetQuotaRequest,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> UserRecord:
    firestore = request.app.state.firestore
    rec = await firestore.get_user_record(
        uid,
        default_quota=TASTYHUB_CONFIG.default_quota(),
        admin_uids=frozenset(TASTYHUB_CONFIG.admin_uids),
    )
    rec.quota = TokenQuota(
        daily_limit=max(0, body.daily_limit),
        monthly_limit=max(0, body.monthly_limit),
    )
    await firestore.save_user_record(rec)
    return rec


@router.put("/admin/users/{uid}/role", response_model=UserRecord)
async def set_role(
    uid: str,
    body: SetRoleRequest,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> UserRecord:
    from fastapi import HTTPException, status

    if body.role not in ("user", "admin"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid role")
    firestore = request.app.state.firestore
    rec = await firestore.get_user_record(
        uid,
        default_quota=TASTYHUB_CONFIG.default_quota(),
        admin_uids=frozenset(TASTYHUB_CONFIG.admin_uids),
    )
    rec.role = body.role
    await firestore.save_user_record(rec)
    return rec


@router.put("/admin/users/{uid}/disabled", response_model=UserRecord)
async def set_disabled(
    uid: str,
    body: SetDisabledRequest,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> UserRecord:
    firestore = request.app.state.firestore
    rec = await firestore.get_user_record(
        uid,
        default_quota=TASTYHUB_CONFIG.default_quota(),
        admin_uids=frozenset(TASTYHUB_CONFIG.admin_uids),
    )
    rec.disabled = body.disabled
    await firestore.save_user_record(rec)
    return rec
