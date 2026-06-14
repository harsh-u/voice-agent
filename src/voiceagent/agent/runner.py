from datetime import datetime, UTC

from loguru import logger

from voiceagent.config import settings
from voiceagent.db.models import Call, TranscriptTurn, CallStatus
from voiceagent.db.session import AsyncSessionLocal
from voiceagent.pipeline.bot import run_pipeline
from voiceagent.telephony.livekit_sip import generate_bot_token
from voiceagent.agent.prompts import build_system_prompt
from voiceagent.observability.client import ingest_call


async def run_agent(
    call_id: str,
    room_name: str,
    agent_config: dict | None,
) -> None:
    """Per-call orchestrator that connects the pipeline to a LiveKit room and
    persists the resulting transcript, recording, and cost data.

    Args:
        call_id: Primary key of the Call record to update.
        room_name: LiveKit room the bot should join.
        agent_config: Optional plain dict with keys system_prompt, voice_id,
            llm_model. Passing a dict (not the SQLAlchemy ORM object) keeps
            this safe to run as a detached background task.
    """
    cfg = agent_config or {}
    system_prompt = build_system_prompt(cfg.get("system_prompt"))
    voice_id = cfg.get("voice_id") or settings.cartesia_voice_id
    llm_model = cfg.get("llm_model") or settings.groq_model
    rag_api_key = cfg.get("rag_api_key") or None
    bot_token = generate_bot_token(room_name)

    logger.info(
        f"run_agent call_id={call_id} room={room_name} "
        f"agent_id={cfg.get('id')} voice={voice_id} model={llm_model}"
    )

    turns: list[tuple[str, str, int | None]] = []
    recording_url_holder: list[str | None] = [None]

    async def on_turn_end(role: str, text: str, latency_ms: int | None) -> None:
        turns.append((role, text, latency_ms))

    async def on_recording_saved(relative_url: str) -> None:
        recording_url_holder[0] = relative_url

    try:
        async with AsyncSessionLocal() as session:
            call = await session.get(Call, call_id)
            if call:
                call.status = CallStatus.active
                await session.commit()
                logger.info(f"Call {call_id} marked active")

        await run_pipeline(
            call_id=call_id,
            room_name=room_name,
            bot_token=bot_token,
            system_prompt=system_prompt,
            voice_id=voice_id,
            llm_model=llm_model,
            on_turn_end=on_turn_end,
            on_recording_saved=on_recording_saved,
            rag_api_key=rag_api_key,
        )
    except Exception as exc:
        logger.error(f"Pipeline error call_id={call_id}: {exc}")
    finally:
        await _finalize_call(call_id, turns, recording_url_holder[0], cfg, voice_id, llm_model)


async def _finalize_call(
    call_id: str,
    turns: list[tuple],
    recording_url: str | None,
    cfg: dict,
    voice_id: str,
    llm_model: str,
) -> None:
    """Write final call metadata, transcript turns, and recording URL to DB.
    Then ship observability data to Convoxio Scope (fire-and-forget).
    """
    started_at_val = None
    ended_at_val = None
    direction_val = "outbound"
    from_number_val = None
    to_number_val = None

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
        if recording_url:
            call.recording_url = recording_url

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
        # Snapshot for observability (after commit so values are final)
        started_at_val = call.started_at
        ended_at_val = call.ended_at
        direction_val = call.direction
        from_number_val = call.from_number
        to_number_val = call.to_number

        logger.info(
            f"Call {call_id} finalized — duration={call.duration_seconds}s "
            f"cost={call.cost_cents}¢ turns={len(turns)} recording={recording_url}"
        )

    # Ship to observability (non-blocking; errors are logged inside ingest_call)
    await ingest_call(
        call_id=call_id,
        agent_config_id=cfg.get("id"),
        direction=direction_val,
        from_number=from_number_val,
        to_number=to_number_val,
        started_at=started_at_val,
        ended_at=ended_at_val,
        turns=turns,
        llm_model=llm_model,
        voice_id=voice_id,
    )
