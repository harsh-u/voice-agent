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

from livekit.agents import AutoSubscribe, JobContext, WorkerOptions, cli
from loguru import logger
from sqlalchemy import select

from voiceagent.agent.runner import run_agent
from voiceagent.config import settings
from voiceagent.db.models import AgentConfig, Call, CallDirection, CallStatus
from voiceagent.db.session import AsyncSessionLocal, create_tables


async def _load_inbound_agent_config() -> dict | None:
    """Pick which AgentConfig to use for an inbound call.

    No explicit per-trunk routing yet, so we fall back to the most recently
    updated AgentConfig — that lets the user pick the active inbound persona
    just by editing/creating one in the dashboard. Returns None if no config
    exists, in which case run_agent falls back to project defaults.
    """
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(AgentConfig).order_by(AgentConfig.updated_at.desc()).limit(1)
        )
        ac = result.scalar_one_or_none()
        if not ac:
            return None
        return {
            "id": ac.id,
            "system_prompt": ac.system_prompt,
            "voice_id": ac.voice_id,
            "llm_model": ac.llm_model,
            "rag_api_key": ac.rag_api_key,
            "rag_kb_id": ac.rag_kb_id,
        }


async def entrypoint(ctx: JobContext) -> None:
    room_name = ctx.room.name
    logger.info(f"Inbound job dispatched → room {room_name}")

    # Mark the job accepted with the LiveKit Agents framework. SUBSCRIBE_NONE
    # keeps this worker-participant silent so it doesn't double up on the
    # pipecat bot, which joins separately with its own token. Without this
    # call the framework warns "job task completed without establishing a
    # connection" and the worker can end up with stuck job slots.
    await ctx.connect(auto_subscribe=AutoSubscribe.SUBSCRIBE_NONE)

    agent_cfg = await _load_inbound_agent_config()
    logger.info(
        f"Inbound agent_config selected: id={agent_cfg.get('id') if agent_cfg else None}"
    )

    call_id = str(uuid.uuid4())
    async with AsyncSessionLocal() as session:
        session.add(
            Call(
                id=call_id,
                agent_config_id=agent_cfg.get("id") if agent_cfg else None,
                direction=CallDirection.inbound,
                status=CallStatus.dialing,
                to_number=None,
                from_number=None,
                livekit_room_name=room_name,
                started_at=datetime.now(UTC),
            )
        )
        await session.commit()

    try:
        await run_agent(call_id, room_name, agent_cfg)
    finally:
        ctx.shutdown(reason="pipeline_complete")


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
