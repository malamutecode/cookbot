from cookbot.hitl.models import HITLCheckpoint
from cookbot.services.firestore import FirestoreService


async def restore_checkpoint(session_id: str, firestore: FirestoreService) -> HITLCheckpoint | None:
    """Return a saved HITL checkpoint if one exists for this session.

    Used when the WebSocket reconnects mid-HITL — the WS handler can re-send
    the checkpoint card to the user without restarting the pipeline.
    """
    return await firestore.get_hitl_checkpoint(session_id)
