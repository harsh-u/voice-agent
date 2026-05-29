import uuid

from livekit import api
from loguru import logger

from voiceagent.config import settings


async def create_room(room_name: str) -> str:
    """Create a LiveKit room and return its name.

    Args:
        room_name: Desired room name.

    Returns:
        The room name after creation.
    """
    lk = api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        await lk.room.create_room(
            api.CreateRoomRequest(
                name=room_name,
                empty_timeout=300,
                max_participants=10,
            )
        )
    finally:
        await lk.aclose()
    logger.info(f"LiveKit room created: {room_name}")
    return room_name


async def dial_outbound(to_number: str, room_name: str) -> str:
    """Dial an outbound SIP call into a LiveKit room.

    Args:
        to_number: The E.164 phone number to dial.
        room_name: The LiveKit room to route the call into.

    Returns:
        The SIP participant identity assigned to this call leg.
    """
    logger.info(
        f"dial_outbound → livekit_url={settings.livekit_url!r} "
        f"sip_trunk_id={settings.livekit_sip_trunk_id!r} "
        f"sip_call_to={to_number} room={room_name}"
    )
    lk = api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        result = await lk.sip.create_sip_participant(
            api.CreateSIPParticipantRequest(
                sip_trunk_id=settings.livekit_sip_trunk_id,
                sip_call_to=to_number,
                room_name=room_name,
                participant_name="caller",
                participant_identity=f"sip-{uuid.uuid4().hex[:8]}",
                play_ringtone=True,
            )
        )
    finally:
        await lk.aclose()
    logger.info(f"Outbound SIP dial to {to_number} in room {room_name}: identity={result.participant_identity} sip_call_id={result.sip_call_id!r}")
    return result.participant_identity


def generate_bot_token(room_name: str) -> str:
    """Generate a LiveKit JWT token for the bot participant.

    Args:
        room_name: The room the bot should join.

    Returns:
        A signed JWT string.
    """
    from pipecat.runner.livekit import generate_token_with_agent

    return generate_token_with_agent(
        room_name=room_name,
        participant_name="voice-bot",
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
