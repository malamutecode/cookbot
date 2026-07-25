"""Admin API — user management + per-user token quotas (STEP 42).

All routes require the caller's own record to be an admin (require_admin). The
first admin is bootstrapped via ADMIN_UIDS (see settings) so there's a way in
before any admin exists.
"""

import asyncio
from datetime import UTC, datetime

import firebase_admin.auth
import structlog
from cookbot.models.password import generate_temp_password, validate_password
from cookbot.models.quota import counter_for, day_key, month_key
from cookbot.models.user import TokenQuota, UserRecord
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel

from app.config.tenant import TASTYHUB_CONFIG
from app.middleware.auth import _get_firebase_app, get_user_record, require_admin

log = structlog.get_logger()

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


class CreateUserRequest(BaseModel):
    """Admin invite form (STEP 44). Quota limits follow the 0 ⇒ unlimited rule."""

    email: str
    display_name: str | None = None
    role: str = "user"      # "user" | "admin"
    daily_limit: int = 0    # 0 ⇒ unlimited
    monthly_limit: int = 0  # 0 ⇒ unlimited


class CreatedUserView(BaseModel):
    """The one and only time the generated password crosses the wire — the SPA
    shows it once and never refetches it (no server-side copy is kept)."""

    record: UserRecord
    temp_password: str


class ChangePasswordRequest(BaseModel):
    new_password: str


class MeView(BaseModel):
    """Minimal self-view so the SPA can decide whether to show the Admin tab
    and whether to force the password-change screen."""

    uid: str
    email: str | None
    display_name: str | None = None
    role: str
    is_admin: bool
    must_change_password: bool = False


def _me_view(record: UserRecord) -> MeView:
    return MeView(
        uid=record.uid,
        email=record.email,
        display_name=record.display_name,
        role=record.role,
        is_admin=record.is_admin,
        must_change_password=record.must_change_password,
    )


@router.get("/me", response_model=MeView)
async def get_me(record: UserRecord = Depends(get_user_record)) -> MeView:
    return _me_view(record)


@router.post("/me/password", response_model=UserRecord)
async def change_own_password(
    body: ChangePasswordRequest,
    request: Request,
    record: UserRecord = Depends(get_user_record),
) -> UserRecord:
    """Set the caller's own password and clear the forced-change flag.

    Depends on `get_user_record`, **not** `require_password_set` — this is the
    one route a password-locked user must be able to call, otherwise a
    temp-password account is locked out permanently.

    The change goes through the Admin SDK rather than the browser's
    `updatePassword` so the flag is cleared by the same party that made the
    change; we never have to trust the client's report of success.
    """
    error = validate_password(body.new_password)
    if error is not None:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=error)

    _get_firebase_app()
    try:
        # firebase_admin's auth SDK is blocking — Architecture Rule 4.
        await asyncio.to_thread(
            firebase_admin.auth.update_user, record.uid, password=body.new_password
        )
    except firebase_admin.auth.UserNotFoundError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Konto nie istnieje.",
        )
    except ValueError as exc:
        # The SDK rejects a password Firebase itself considers invalid.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    record.must_change_password = False
    await request.app.state.firestore.save_user_record(record)
    log.info("password_changed", uid=record.uid)
    return record


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


@router.post("/admin/users", response_model=CreatedUserView, status_code=status.HTTP_201_CREATED)
async def create_user(
    body: CreateUserRequest,
    request: Request,
    _admin: UserRecord = Depends(require_admin),
) -> CreatedUserView:
    """Create a Firebase account with a generated temp password (STEP 44).

    The password is returned exactly once (nothing server-side keeps it) and the
    new record carries `must_change_password=True`, which `require_password_set`
    enforces on every product route until the user picks their own.
    """
    email = body.email.strip()
    if not email or "@" not in email:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Podaj poprawny adres e-mail.",
        )
    if body.role not in ("user", "admin"):
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="Invalid role")

    display_name = (body.display_name or "").strip() or None
    temp_password = generate_temp_password()

    _get_firebase_app()
    kwargs: dict[str, str] = {"email": email, "password": temp_password}
    if display_name:
        kwargs["display_name"] = display_name
    try:
        # firebase_admin's auth SDK is blocking — Architecture Rule 4.
        created = await asyncio.to_thread(firebase_admin.auth.create_user, **kwargs)
    except firebase_admin.auth.EmailAlreadyExistsError:
        # Never silently reset a live user's password — refuse instead.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Konto z tym adresem e-mail już istnieje.",
        )
    except ValueError as exc:
        # The SDK validates email/password shape before the network call.
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    record = UserRecord(
        uid=created.uid,
        email=email,
        display_name=display_name,
        role=body.role,
        quota=TokenQuota(
            daily_limit=max(0, body.daily_limit),
            monthly_limit=max(0, body.monthly_limit),
        ),
        must_change_password=True,
    )
    await request.app.state.firestore.save_user_record(record)
    log.info("user_created", uid=record.uid, role=record.role, by=_admin.uid)
    return CreatedUserView(record=record, temp_password=temp_password)


@router.delete("/admin/users/{uid}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_user(
    uid: str,
    request: Request,
    admin: UserRecord = Depends(require_admin),
) -> None:
    """Remove the Firebase account and its Firestore record.

    Refuses self-deletion: an admin deleting themselves mid-session would leave
    the panel unreachable with no way back in short of ADMIN_UIDS + a redeploy.
    """
    if uid == admin.uid:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Nie możesz usunąć własnego konta.",
        )

    _get_firebase_app()
    try:
        # firebase_admin's auth SDK is blocking — Architecture Rule 4.
        await asyncio.to_thread(firebase_admin.auth.delete_user, uid)
    except firebase_admin.auth.UserNotFoundError:
        # Already gone in Firebase — still drop the orphaned record below.
        log.warning("delete_user_not_in_firebase", uid=uid)

    await request.app.state.firestore.delete_user_record(uid)
    log.info("user_deleted", uid=uid, by=admin.uid)


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
