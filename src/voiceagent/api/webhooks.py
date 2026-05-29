"""LiveKit webhook handler for inbound call routing.

LiveKit sends a POST to /webhooks/livekit when room events occur (participant
connected/disconnected, room started/finished, etc.).  When a SIP participant
joins a room that already has a Call record, we spawn the voice pipeline for
inbound call handling.
"""

import asyncio

from fastapi import APIRouter, HTTPException, Request
from livekit.api import TokenVerifier, WebhookReceiver
from loguru import logger
from sqlalchemy import select

from voiceagent.config import settings
from voiceagent.db.models import AgentConfig, Call, CallDirection, CallStatus
from voiceagent.db.session import AsyncSessionLocal
from voiceagent.agent.runner import run_agent

router = APIRouter(prefix="/webhooks", tags=["webhooks"])

_webhook_receiver = WebhookReceiver(
    TokenVerifier(settings.livekit_api_key, settings.livekit_api_secret)
)


@router.post("/livekit")
async def livekit_webhook(request: Request):
    """Receive LiveKit room event webhooks.

    Signature is verified using the LiveKit API secret — spoofed requests
    are rejected with 401 before any DB access occurs.
    """
    body_bytes = await request.body()
    auth_header = request.headers.get("Authorization", "")

    try:
        event = _webhook_receiver.receive(body_bytes.decode(), auth_header)
    except Exception as exc:
        logger.warning(f"Webhook signature verification failed: {exc}")
        raise HTTPException(status_code=401, detail="Invalid webhook signature")

    event_type: str = event.event
    room_name: str = event.room.name if event.room else ""
    participant_identity: str = event.participant.identity if event.participant else ""

    logger.debug(
        f"LiveKit webhook: event={event_type} room={room_name} participant={participant_identity}"
    )

    if event_type != "participant_connected":
        return {"status": "ignored", "reason": "unhandled_event"}

    # Only act on SIP participants joining (identity starts with "sip-")
    if not participant_identity.startswith("sip-"):
        return {"status": "ignored", "reason": "not_sip_participant"}

    if not room_name:
        return {"status": "ignored", "reason": "missing_room_name"}

    # Look up the Call record for this room
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Call).where(Call.livekit_room_name == room_name)
        )
        call = result.scalar_one_or_none()

        if not call:
            # No existing call record — this is an inbound call; create one
            from datetime import datetime, UTC
            import uuid

            call_id = str(uuid.uuid4())
            call = Call(
                id=call_id,
                direction=CallDirection.inbound,
                status=CallStatus.dialing,
                from_number=None,  # not available at this stage
                livekit_room_name=room_name,
                started_at=datetime.now(UTC),
            )
            session.add(call)
            await session.commit()
            logger.info(f"Inbound call record created: {call_id} for room {room_name}")
        else:
            call_id = call.id
            logger.info(f"SIP participant joined existing call {call_id} in room {room_name}")

        # Detach agent_config before closing session so it can be passed to the task
        agent_config: AgentConfig | None = None
        if call.agent_config_id:
            agent_config = await session.get(AgentConfig, call.agent_config_id)

    # Spawn the pipeline as a background task
    asyncio.create_task(run_agent(call_id, room_name, agent_config))
    logger.info(f"Pipeline task spawned for call {call_id} (inbound webhook)")

    return {"status": "accepted", "call_id": call_id}
