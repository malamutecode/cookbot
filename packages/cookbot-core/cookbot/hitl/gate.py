import asyncio
from datetime import UTC, datetime
from uuid import uuid4

from cookbot.exceptions import HITLTimeoutError
from cookbot.hitl.models import HITLCheckpoint, HITLResponse
from cookbot.models.recipe import Recipe
from cookbot.services.firestore import FirestoreService


class HITLGate:
    def __init__(self, session_id: str, firestore: FirestoreService) -> None:
        self._session_id = session_id
        self._firestore = firestore
        self._checkpoint_q: asyncio.Queue[HITLCheckpoint] = asyncio.Queue(1)
        self._response_q: asyncio.Queue[HITLResponse] = asyncio.Queue(1)

    async def suspend(self, recipe: Recipe, round_number: int, timeout: float = 3600.0) -> HITLResponse:
        """Called by the pipeline. Saves checkpoint and blocks until the human responds."""
        checkpoint = HITLCheckpoint(
            checkpoint_id=str(uuid4()),
            session_id=self._session_id,
            recipe=recipe,
            round_number=round_number,
            created_at=datetime.now(UTC),
        )
        await self._firestore.save_hitl_checkpoint(checkpoint)
        await self._checkpoint_q.put(checkpoint)
        try:
            response = await asyncio.wait_for(self._response_q.get(), timeout=timeout)
        except asyncio.TimeoutError:
            raise HITLTimeoutError(f"No response received within {timeout}s for session {self._session_id}")
        await self._firestore.clear_hitl_checkpoint(self._session_id)
        return response

    async def get_checkpoint(self) -> HITLCheckpoint:
        """Called by the WS handler. Waits for the pipeline to produce a checkpoint."""
        return await self._checkpoint_q.get()

    async def submit_response(self, response: HITLResponse) -> None:
        """Called by the WS handler. Injects the human response back into the pipeline."""
        await self._response_q.put(response)
