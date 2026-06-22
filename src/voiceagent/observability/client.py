"""Post-call observability ingestion to Convoxio Scope (VoxScope).

Called once after each call completes. Assembles an IngestBatch from the
collected turn data and ships it to the observability service. Errors are
logged but never raised — telemetry must not impact call delivery.
"""
from __future__ import annotations

import uuid
from datetime import datetime, UTC
from typing import Optional

import httpx
from loguru import logger

from voiceagent.config import settings


async def ingest_call(
    *,
    call_id: str,
    agent_config_id: Optional[str],
    direction: str,
    from_number: Optional[str],
    to_number: Optional[str],
    started_at: Optional[datetime],
    ended_at: Optional[datetime],
    turns: list[tuple[str, str, Optional[int]]],
    llm_model: str,
    voice_id: str,
) -> None:
    """Ship call trace + spans to the observability service.

    Args:
        call_id: Primary key of the Call — used as trace_id.
        agent_config_id: AgentConfig ID for meta tagging.
        direction: "inbound" or "outbound".
        from_number / to_number: SIP leg numbers.
        started_at / ended_at: Call timestamps.
        turns: List of (role, text, latency_ms) tuples from the call.
        llm_model: Groq model name used.
        voice_id: Cartesia voice ID used.
    """
    # The unified backend manages observability internally: if no explicit
    # ingest key is configured, fall back to the deterministic workspace key so
    # the worker can ship telemetry to /observability/v1/ingest/batch with no
    # manual key wiring.
    from voiceagent.integrations import default_ingest_key
    ingest_key = settings.voxscope_api_key or default_ingest_key()

    try:
        batch = _build_batch(
            call_id=call_id,
            agent_config_id=agent_config_id,
            direction=direction,
            from_number=from_number,
            to_number=to_number,
            started_at=started_at,
            ended_at=ended_at,
            turns=turns,
            llm_model=llm_model,
            voice_id=voice_id,
        )

        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                f"{settings.voxscope_url.rstrip('/')}/v1/ingest/batch",
                json=batch,
                headers={"Authorization": f"Bearer {ingest_key}"},
            )
            if resp.status_code == 202:
                logger.info(f"[observability] ingested call {call_id} — {len(turns)} turns")
            else:
                logger.warning(
                    f"[observability] ingest returned {resp.status_code} for call {call_id}: {resp.text[:200]}"
                )
    except Exception as exc:
        logger.warning(f"[observability] failed to ingest call {call_id}: {exc}")


def _build_batch(
    *,
    call_id: str,
    agent_config_id: Optional[str],
    direction: str,
    from_number: Optional[str],
    to_number: Optional[str],
    started_at: Optional[datetime],
    ended_at: Optional[datetime],
    turns: list[tuple[str, str, Optional[int]]],
    llm_model: str,
    voice_id: str,
) -> dict:
    now = datetime.now(UTC)
    trace_started = started_at or now
    trace_ended = ended_at or now

    ingest_turns = []
    spans = []

    for i, (role, text, latency_ms) in enumerate(turns):
        turn_id = f"{call_id}-t{i}"

        if role == "user":
            ingest_turns.append({
                "turn_id": turn_id,
                "turn_index": i,
                "role": "user",
                "user_transcript": text,
                "started_at": trace_started.isoformat(),
            })
            # STT span per user turn
            spans.append({
                "span_id": f"{turn_id}-stt",
                "turn_id": turn_id,
                "trace_id": call_id,
                "component": "stt",
                "name": "deepgram.transcription",
                "start_ms": 0.0,
                "end_ms": float(latency_ms) if latency_ms else None,
                "fields": {
                    "provider": "deepgram",
                    "model": settings.deepgram_model,
                    "final_transcript": text[:500],
                },
            })
        else:
            ingest_turns.append({
                "turn_id": turn_id,
                "turn_index": i,
                "role": "agent",
                "agent_transcript": text,
                "started_at": trace_started.isoformat(),
                # Response latency (end-of-user-speech → assistant response).
                "response_latency_ms": float(latency_ms) if latency_ms else None,
                "ttfb_ms": float(latency_ms) if latency_ms else None,
            })
            # LLM span per assistant turn — latency_ms is TTFT proxy
            if latency_ms is not None:
                spans.append({
                    "span_id": f"{turn_id}-llm",
                    "turn_id": turn_id,
                    "trace_id": call_id,
                    "component": "llm",
                    "name": "groq.completion",
                    "start_ms": 0.0,
                    "end_ms": float(latency_ms),
                    "ttfb_ms": float(latency_ms),
                    "fields": {
                        "provider": "groq",
                        "model": llm_model,
                        "ttft_ms": float(latency_ms),
                    },
                })
            # TTS span per assistant turn
            spans.append({
                "span_id": f"{turn_id}-tts",
                "turn_id": turn_id,
                "trace_id": call_id,
                "component": "tts",
                "name": "cartesia.synthesis",
                "start_ms": 0.0,
                "fields": {
                    "provider": "cartesia",
                    "voice_id": voice_id,
                    "chars": len(text),
                },
            })

    # Telephony span for the whole call
    if started_at and ended_at:
        duration_s = (ended_at - started_at).total_seconds()
        spans.append({
            "span_id": f"{call_id}-tel",
            "turn_id": f"{call_id}-t0",
            "trace_id": call_id,
            "component": "telephony",
            "name": "livekit.sip",
            "start_ms": 0.0,
            "end_ms": duration_s * 1000,
            "fields": {
                "kind": "sip",
                "direction": direction,
                "minutes": round(duration_s / 60, 4),
            },
        })

    return {
        "sdk_version": "0.1.0",
        "trace": {
            "trace_id": call_id,
            "external_call_id": call_id,
            "framework": settings.voice_engine,
            "started_at": trace_started.isoformat(),
            "ended_at": trace_ended.isoformat(),
            "status": "completed",
            "meta": {
                "agent_config_id": agent_config_id,
                "direction": direction,
                "from_number": from_number,
                "to_number": to_number,
            },
        },
        "turns": ingest_turns,
        "spans": spans,
    }
