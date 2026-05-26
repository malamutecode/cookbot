from datetime import UTC, datetime, timedelta

import structlog
from google.cloud.firestore_v1.async_client import AsyncClient

from cookbot.hitl.models import HITLCheckpoint
from cookbot.models.session import Message, Session, SessionStatus

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
