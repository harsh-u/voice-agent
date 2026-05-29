"""LiveKit agent worker — handles inbound calls dispatched by LiveKit.

Run with:
    PYTHONPATH=src .venv/bin/python worker.py dev

The "dev" subcommand registers this worker with LiveKit Cloud under the
agent name "voice-agent". When an inbound SIP call hits the dispatch rule
created by inbound_provisioner.py, LiveKit creates a room and dispatches a
job here. We persist a Call record and hand off to the existing run_agent.
"""

import uuid
from datetime import UTC, datetime

from livekit.agents import JobContext, WorkerOptions, cli
from loguru import logger

from voiceagent.agent.runner import run_agent
from voiceagent.config import settings
from voiceagent.db.models import Call, CallDirection, CallStatus
from voiceagent.db.session import AsyncSessionLocal, create_tables


async def entrypoint(ctx: JobContext) -> None:
    room_name = ctx.room.name
    logger.info(f"Inbound job dispatched → room {room_name}")

    call_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        session.add(
            Call(
                id=call_id,
                direction=CallDirection.inbound,
                status=CallStatus.dialing,
                to_number=None,
                from_number=None,
                livekit_room_name=room_name,
                started_at=datetime.now(UTC),
            )
        )
        await session.commit()

    await run_agent(call_id, room_name, None)


async def prewarm() -> None:
    await create_tables()


if __name__ == "__main__":
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name="voice-agent",
            ws_url=settings.livekit_url,
            api_key=settings.livekit_api_key,
            api_secret=settings.livekit_api_secret,
        )
    )
