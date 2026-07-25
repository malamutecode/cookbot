from datetime import UTC, datetime, timedelta

import structlog
from google.cloud.firestore_v1.async_client import AsyncClient

from cookbot.hitl.models import HITLCheckpoint
from cookbot.models.session import Message, Session
from cookbot.models.spizarnia import Spizarnia, SpizarniaItem
from cookbot.models.user import (
    DEFAULT_SOURCES,
    TokenQuota,
    UsageCounter,
    UserProfile,
    UserRecord,
    UserSearchPrefs,
)

log = structlog.get_logger()


class FirestoreService:
    def __init__(self, project_id: str, database_id: str, tenant_id: str) -> None:
        self._tenant_id = tenant_id
        self._client = AsyncClient(project=project_id, database=database_id)

    def _session_ref(self, session_id: str):  # type: ignore[return]
        return self._client.collection("sessions").document(self._tenant_id).collection("sessions").document(session_id)

    async def save_message(self, session_id: str, message: Message) -> None:
        ref = self._session_ref(session_id)
        doc = await ref.get()
        messages: list[dict] = []
        if doc.exists:
            messages = doc.to_dict().get("messages", [])  # type: ignore[union-attr]
        messages.append(message.model_dump(mode="json"))
        await ref.set({"messages": messages}, merge=True)

    async def get_messages(self, session_id: str) -> list[Message]:
        doc = await self._session_ref(session_id).get()
        if not doc.exists:
            return []
        raw: list[dict] = doc.to_dict().get("messages", [])  # type: ignore[union-attr]
        return [Message.model_validate(m) for m in raw]

    async def save_chat_state(self, session_id: str, state: dict) -> None:
        """Persist the resumable conversation snapshot (see
        cookbot.agents.chat.dump_chat_state) on the session document."""
        await self._session_ref(session_id).set({"chat_state": state}, merge=True)

    async def get_chat_state(self, session_id: str) -> dict | None:
        doc = await self._session_ref(session_id).get()
        if not doc.exists:
            return None
        return doc.to_dict().get("chat_state")  # type: ignore[union-attr]

    async def save_session(self, session: Session) -> None:
        await self._session_ref(session.session_id).set(session.model_dump(mode="json"))

    async def get_session(self, session_id: str) -> Session | None:
        doc = await self._session_ref(session_id).get()
        if not doc.exists:
            return None
        return Session.model_validate(doc.to_dict())

    async def save_hitl_checkpoint(self, checkpoint: HITLCheckpoint) -> None:
        await self._session_ref(checkpoint.session_id).set(
            {"hitl_checkpoint": checkpoint.model_dump(mode="json")},
            merge=True,
        )

    async def get_hitl_checkpoint(self, session_id: str) -> HITLCheckpoint | None:
        doc = await self._session_ref(session_id).get()
        if not doc.exists:
            return None
        data = doc.to_dict()  # type: ignore[union-attr]
        raw = data.get("hitl_checkpoint")
        if raw is None:
            return None
        return HITLCheckpoint.model_validate(raw)

    async def clear_hitl_checkpoint(self, session_id: str) -> None:
        from google.cloud.firestore_v1 import DELETE_FIELD

        await self._session_ref(session_id).update({"hitl_checkpoint": DELETE_FIELD})

    def _user_ref(self, uid: str):  # type: ignore[return]
        return self._client.collection("users").document(uid)

    async def save_user_profile(self, profile: UserProfile) -> None:
        await self._user_ref(profile.uid).set(profile.model_dump(mode="json"))

    async def get_user_profile(self, uid: str) -> UserProfile | None:
        doc = await self._user_ref(uid).get()
        if not doc.exists:
            return None
        return UserProfile.model_validate(doc.to_dict())

    def _spizarnia_ref(self, uid: str):  # type: ignore[return]
        return self._client.collection("users").document(uid).collection("spizarnia").document("items")

    async def get_spizarnia(self, uid: str) -> Spizarnia:
        doc = await self._spizarnia_ref(uid).get()
        if not doc.exists:
            return Spizarnia(uid=uid)
        return Spizarnia.model_validate({**doc.to_dict(), "uid": uid})

    async def save_spizarnia(self, spizarnia: Spizarnia) -> None:
        await self._spizarnia_ref(spizarnia.uid).set(spizarnia.model_dump(mode="json"))

    async def add_spizarnia_items(self, uid: str, items: list[SpizarniaItem]) -> None:
        spizarnia = await self.get_spizarnia(uid)
        by_name = {item.name.lower(): item for item in spizarnia.items}
        for new_item in items:
            key = new_item.name.lower()
            if key in by_name:
                by_name[key] = SpizarniaItem(
                    name=by_name[key].name,
                    quantity=new_item.quantity,
                    added_at=by_name[key].added_at,
                )
            else:
                by_name[key] = new_item
        spizarnia.items = list(by_name.values())
        spizarnia.updated_at = datetime.now(UTC)
        await self.save_spizarnia(spizarnia)

    async def remove_spizarnia_item(self, uid: str, item_name: str) -> None:
        spizarnia = await self.get_spizarnia(uid)
        key = item_name.lower()
        spizarnia.items = [i for i in spizarnia.items if i.name.lower() != key]
        spizarnia.updated_at = datetime.now(UTC)
        await self.save_spizarnia(spizarnia)

    def _search_prefs_ref(self, uid: str):  # type: ignore[return]
        return self._client.collection("users").document(uid).collection("prefs").document("search")

    async def get_search_prefs(self, uid: str) -> UserSearchPrefs:
        doc = await self._search_prefs_ref(uid).get()
        if not doc.exists:
            prefs = UserSearchPrefs(uid=uid, sources=list(DEFAULT_SOURCES))
            await self.save_search_prefs(prefs)
            return prefs
        return UserSearchPrefs.model_validate({**doc.to_dict(), "uid": uid})

    async def save_search_prefs(self, prefs: UserSearchPrefs) -> None:
        await self._search_prefs_ref(prefs.uid).set(prefs.model_dump(mode="json"))

    # ── User records + token quotas (STEP 42) ────────────────────────────────

    def _user_record_ref(self, uid: str):  # type: ignore[return]
        # The record lives ON the parent users/{uid} doc (under a `record` map) so
        # the parent is a real document that list_user_records()'s stream returns.
        # Writing only to a subcollection leaves the parent doc non-existent in
        # Firestore and it would be skipped by a collection stream.
        return self._client.collection("users").document(uid)

    def _usage_ref(self, uid: str, period_key: str):  # type: ignore[return]
        return self._client.collection("users").document(uid).collection("usage").document(period_key)

    async def get_user_record(
        self,
        uid: str,
        *,
        default_quota: TokenQuota | None = None,
        admin_uids: frozenset[str] | set[str] = frozenset(),
        email: str | None = None,
        display_name: str | None = None,
    ) -> UserRecord:
        """Load a user's account record, creating a default one on first sight.

        A new record inherits `default_quota` (the tenant defaults) and is seeded
        as an admin when its uid is in `admin_uids` (bootstrap path so there's a
        way in before any admin exists). `email`/`display_name` backfill an
        existing record that predates those fields, so the admin table can render
        an identity without a Firebase read."""
        doc = await self._user_record_ref(uid).get()
        raw = doc.to_dict().get("record") if doc.exists else None  # type: ignore[union-attr]
        if raw is None:
            rec = UserRecord(
                uid=uid,
                email=email,
                display_name=display_name,
                role="admin" if uid in admin_uids else "user",
                quota=default_quota or TokenQuota(),
            )
            await self.save_user_record(rec)
            return rec
        rec = UserRecord.model_validate({**raw, "uid": uid})
        dirty = False
        # Keep the seeded admins admin even if the record predates the seed.
        if uid in admin_uids and rec.role != "admin":
            rec.role = "admin"
            dirty = True
        if email and not rec.email:
            rec.email = email
            dirty = True
        if display_name and not rec.display_name:
            rec.display_name = display_name
            dirty = True
        if dirty:
            await self.save_user_record(rec)
        return rec

    async def save_user_record(self, rec: UserRecord) -> None:
        await self._user_record_ref(rec.uid).set(
            {"record": rec.model_dump(mode="json")}, merge=True
        )

    async def find_user_record(self, uid: str) -> UserRecord | None:
        """Read a user's record WITHOUT creating one (unlike `get_user_record`).

        Used by authorization paths that must distinguish "this account exists"
        from "this uid has a valid token" — creating on read there would turn any
        authenticated uid into an authorized one."""
        doc = await self._user_record_ref(uid).get()
        raw = doc.to_dict().get("record") if doc.exists else None  # type: ignore[union-attr]
        if raw is None:
            return None
        return UserRecord.model_validate({**raw, "uid": uid})

    async def delete_user_record(self, uid: str) -> None:
        """Remove a user's account record document (`users/{uid}`).

        Deleting the parent doc drops the record, profile and any other fields
        stored on it. Firestore subcollections (`spizarnia`, `prefs`, `usage`)
        are NOT cascaded by a parent delete — they are orphaned, which is
        harmless: nothing reads them without a record, and a re-created uid is
        never reused by Firebase."""
        await self._user_record_ref(uid).delete()

    async def list_user_records(self) -> list[UserRecord]:
        """All account records (admin listing) — the users collection stream,
        picking up the `record` map on each user document."""
        records: list[UserRecord] = []
        async for user_doc in self._client.collection("users").stream():
            raw = (user_doc.to_dict() or {}).get("record")
            if raw is not None:
                records.append(UserRecord.model_validate({**raw, "uid": user_doc.id}))
        return records

    async def get_usage_counter(self, uid: str, period_key: str) -> UsageCounter:
        """Read one window's counter. Returns a zeroed counter for the window if
        none is stored yet (a past-window doc under a different key is simply not
        read — callers pass the *current* period_key)."""
        doc = await self._usage_ref(uid, period_key).get()
        if not doc.exists:
            return UsageCounter(period_key=period_key, tokens_used=0)
        return UsageCounter.model_validate(doc.to_dict())

    async def add_usage(self, uid: str, period_keys: list[str], tokens: int) -> None:
        """Atomically add `tokens` to each window counter (day + month).

        Each counter doc is keyed by its period_key, so a new period simply
        writes a fresh doc — the increment on a non-existent field starts from 0
        (lazy reset by construction, no cron needed)."""
        from google.cloud.firestore_v1 import Increment

        for period_key in period_keys:
            await self._usage_ref(uid, period_key).set(
                {"period_key": period_key, "tokens_used": Increment(tokens)},
                merge=True,
            )

    async def expire_old_sessions(self, ttl_hours: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(hours=ttl_hours)
        collection = (
            self._client.collection("sessions")
            .document(self._tenant_id)
            .collection("sessions")
        )
        count = 0
        async for doc in collection.stream():
            data = doc.to_dict() or {}
            expires_at_raw = data.get("expires_at")
            if expires_at_raw is None:
                continue
            # Firestore returns datetime objects for timestamp fields
            expires_at = (
                expires_at_raw
                if isinstance(expires_at_raw, datetime)
                else datetime.fromisoformat(str(expires_at_raw))
            )
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=UTC)
            if expires_at < cutoff:
                await doc.reference.delete()
                count += 1
        log.info("expired_sessions", tenant=self._tenant_id, count=count)
        return count
