from datetime import UTC, datetime, timedelta

import structlog
from google.cloud.firestore_v1.async_client import AsyncClient

from cookbot.hitl.models import HITLCheckpoint
from cookbot.models.session import Message, Session, SessionStatus
from cookbot.models.spizarnia import Spizarnia, SpizarniaItem
from cookbot.models.user import DEFAULT_SOURCES, UserProfile, UserSearchPrefs

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
