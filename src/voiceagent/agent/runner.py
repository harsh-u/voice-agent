import asyncio
from datetime import datetime, UTC

from loguru import logger

from voiceagent.config import settings
from voiceagent.db.models import Call, TranscriptTurn, AgentConfig, CallStatus
from voiceagent.db.session import AsyncSessionLocal
from voiceagent.pipeline.bot import run_pipeline
from voiceagent.telephony.livekit_sip import generate_bot_token
from voiceagent.agent.prompts import build_system_prompt


async def run_agent(
    call_id: str,
    room_name: str,
    agent_config: AgentConfig | None,
) -> None:
    """Per-call orchestrator that connects the pipeline to a LiveKit room and
    persists the resulting transcript and cost data.

    Args:
        call_id: Primary key of the Call record to update.
        room_name: LiveKit room the bot should join.
        agent_config: Optional AgentConfig driving prompt and voice choices.
    """
    system_prompt = build_system_prompt(
        agent_config.system_prompt if agent_config else None
    )
    voice_id = (
        agent_config.voice_id if agent_config and agent_config.voice_id
        else settings.cartesia_voice_id
    )
    llm_model = (
        agent_config.llm_model if agent_config and agent_config.llm_model
        else settings.groq_model
    )
    bot_token = generate_bot_token(room_name)

    turns: list[tuple[str, str, int | None]] = []

    async def on_turn_end(role: str, text: str, latency_ms: int | None) -> None:
        turns.append((role, text, latency_ms))

    try:
        async with AsyncSessionLocal() as session:
            call = await session.get(Call, call_id)
            if call:
                call.status = CallStatus.active
                await session.commit()
                logger.info(f"Call {call_id} marked active")

        await run_pipeline(
            room_name=room_name,
            bot_token=bot_token,
            system_prompt=system_prompt,
            voice_id=voice_id,
            llm_model=llm_model,
            on_turn_end=on_turn_end,
        )
    except Exception as exc:
        logger.error(f"Pipeline error call_id={call_id}: {exc}")
    finally:
        await _finalize_call(call_id, turns)


async def _finalize_call(call_id: str, turns: list[tuple]) -> None:
    """Write final call metadata and transcript turns to the database.

    Args:
        call_id: Primary key of the Call to finalize.
        turns: List of (role, text, latency_ms) tuples collected during the call.
    """
    async with AsyncSessionLocal() as session:
        call = await session.get(Call, call_id)
        if not call:
            logger.warning(f"_finalize_call: call {call_id} not found in DB")
            return

        call.ended_at = datetime.now(UTC)
        if call.started_at:
            call.duration_seconds = int(
                (call.ended_at - call.started_at).total_seconds()
            )
            total_cpm = (
                settings.cost_stt_cpm
                + settings.cost_llm_cpm
                + settings.cost_tts_cpm
                + settings.cost_telephony_cpm
            )
            call.cost_cents = round((call.duration_seconds / 60) * total_cpm, 4)

        call.status = CallStatus.completed

        for role, text, latency_ms in turns:
            session.add(
                TranscriptTurn(
                    call_id=call_id,
                    role=role,
                    text=text,
                    latency_ms=latency_ms,
                )
            )

        await session.commit()
        logger.info(
            f"Call {call_id} finalized — duration={call.duration_seconds}s "
            f"cost={call.cost_cents}¢ turns={len(turns)}"
        )
