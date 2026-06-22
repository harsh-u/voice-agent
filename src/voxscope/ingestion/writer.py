"""
Async bulk writer for ingestion batches.

Handles:
- Upsert traces/turns/spans (ON CONFLICT DO NOTHING for spans).
- Derived latency computation (response_latency_ms, ttfb_ms, per-trace p-tiles).
- Stub parent creation for orphaned spans.
- Edge-case handling per §9 (out-of-order, missing parents, clamp negative durations).
"""
from __future__ import annotations
import logging
import statistics
from datetime import datetime, UTC
from typing import Optional

from sqlalchemy import select, update, func
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.ext.asyncio import AsyncSession

from voxscope.db.models import Trace, Turn, Span, ComponentType, TraceStatus, TurnRole
from voxscope.db.session import AsyncSessionLocal
from voxscope.schemas.ingest import IngestBatch
from voxscope.ingestion.sampling import should_write_spans

logger = logging.getLogger(__name__)


async def process_batch(batch: IngestBatch, project_id: str, sample_rate: float, slow_threshold_ms: float) -> int:
    """
    Process one IngestBatch. Returns count of spans accepted.
    All DB writes happen here; called from the background drain task.
    """
    async with AsyncSessionLocal() as session:
        try:
            count = await _write_batch(session, batch, project_id, sample_rate, slow_threshold_ms)
            await session.commit()
            return count
        except Exception as exc:
            await session.rollback()
            logger.error(f"[writer] batch write failed: {exc}", exc_info=True)
            return 0


async def _write_batch(
    session: AsyncSession,
    batch: IngestBatch,
    project_id: str,
    sample_rate: float,
    slow_threshold_ms: float,
) -> int:
    trace_data = batch.trace
    trace_id = trace_data.trace_id

    # Check if any span has an error flag
    has_error = (
        trace_data.status == "error"
        or any(s.error for s in batch.spans)
    )

    # Naive response_latency_ms estimate from spans for tail-sampling decision
    # (we'll compute it properly after writing spans)
    prelim_latency = _estimate_response_latency(batch.spans)

    write_spans, sampled_flag = should_write_spans(
        trace_id, sample_rate, slow_threshold_ms,
        prelim_latency, has_error,
    )

    # --- Upsert Trace ---
    existing_trace = await session.get(Trace, trace_id)
    if existing_trace is None:
        trace_row = Trace(
            id=trace_id,
            project_id=project_id,
            external_call_id=trace_data.external_call_id,
            framework=trace_data.framework or "custom",
            status=trace_data.status or TraceStatus.active,
            started_at=trace_data.started_at or datetime.now(UTC),
            ended_at=trace_data.ended_at,
            sampled=sampled_flag,
            meta=trace_data.meta,
        )
        session.add(trace_row)
        await session.flush()
        existing_trace = trace_row
    else:
        # Update mutable fields
        if trace_data.ended_at:
            existing_trace.ended_at = trace_data.ended_at
        if trace_data.status and trace_data.status != "active":
            existing_trace.status = trace_data.status
        if trace_data.meta:
            existing_trace.meta = {**(existing_trace.meta or {}), **trace_data.meta}
        # Always update sampled to True if we're now writing spans
        if sampled_flag:
            existing_trace.sampled = True

    if not write_spans:
        # Metadata-only: update cost from span fields, then return
        cost = _extract_cost_from_spans(batch.spans)
        if cost:
            existing_trace.cost_cents = (existing_trace.cost_cents or 0.0) + cost
        return 0

    # --- Upsert Turns ---
    turn_ids_written: set[str] = set()
    for turn_data in batch.turns:
        turn_id = turn_data.turn_id
        existing_turn = await session.get(Turn, turn_id)
        if existing_turn is None:
            turn_row = Turn(
                id=turn_id,
                trace_id=trace_id,
                project_id=project_id,
                turn_index=turn_data.turn_index,
                role=turn_data.role or TurnRole.agent,
                user_transcript=turn_data.user_transcript,
                agent_transcript=turn_data.agent_transcript,
                started_at=turn_data.started_at or datetime.now(UTC),
                ended_at=turn_data.ended_at,
                interrupted=turn_data.interrupted,
                response_latency_ms=turn_data.response_latency_ms,
                ttfb_ms=turn_data.ttfb_ms,
            )
            session.add(turn_row)
        else:
            if turn_data.ended_at:
                existing_turn.ended_at = turn_data.ended_at
            if turn_data.user_transcript:
                existing_turn.user_transcript = turn_data.user_transcript
            if turn_data.agent_transcript:
                existing_turn.agent_transcript = turn_data.agent_transcript
            if turn_data.interrupted:
                existing_turn.interrupted = True
            if turn_data.response_latency_ms is not None:
                existing_turn.response_latency_ms = turn_data.response_latency_ms
            if turn_data.ttfb_ms is not None:
                existing_turn.ttfb_ms = turn_data.ttfb_ms
        turn_ids_written.add(turn_id)

    await session.flush()

    # --- Ensure stub turns for any orphaned spans ---
    span_turn_ids = {s.turn_id for s in batch.spans}
    missing_turn_ids = span_turn_ids - turn_ids_written
    for missing_tid in missing_turn_ids:
        existing_turn = await session.get(Turn, missing_tid)
        if existing_turn is None:
            stub_turn = Turn(
                id=missing_tid,
                trace_id=trace_id,
                project_id=project_id,
                turn_index=0,
                role=TurnRole.agent,
                started_at=datetime.now(UTC),
            )
            session.add(stub_turn)
    if missing_turn_ids:
        await session.flush()

    # --- Upsert Spans (ON CONFLICT DO NOTHING via unique constraint) ---
    accepted = 0
    for span_data in batch.spans:
        span_id = span_data.span_id

        # Clamp negative durations (§9.4)
        start_ms = span_data.start_ms
        end_ms = span_data.end_ms
        duration_ms: Optional[float] = None
        if end_ms is not None:
            raw_duration = end_ms - start_ms
            if raw_duration < 0:
                logger.warning(f"[writer] negative duration for span {span_id}, clamping to 0")
                raw_duration = 0.0
                end_ms = start_ms
            duration_ms = raw_duration

        existing_span = await session.get(Span, span_id)
        if existing_span is not None:
            # Duplicate — ON CONFLICT DO NOTHING semantics
            continue

        span_row = Span(
            id=span_id,
            turn_id=span_data.turn_id,
            trace_id=trace_id,
            project_id=project_id,
            component=span_data.component,
            name=span_data.name,
            start_ms=start_ms,
            end_ms=end_ms,
            duration_ms=duration_ms,
            ttfb_ms=span_data.ttfb_ms,
            error=span_data.error,
            fields=span_data.fields,
        )
        session.add(span_row)
        accepted += 1

    await session.flush()

    # --- Compute derived latency per turn ---
    all_affected_turn_ids = span_turn_ids | turn_ids_written
    for turn_id in all_affected_turn_ids:
        await _compute_turn_latency(session, turn_id)

    # --- Compute per-trace percentiles at finalize ---
    await _compute_trace_percentiles(session, trace_id)

    # --- Update trace turn_count and cost ---
    turn_count_result = await session.execute(
        select(func.count()).select_from(Turn).where(Turn.trace_id == trace_id)
    )
    existing_trace.turn_count = turn_count_result.scalar() or 0

    cost = _extract_cost_from_spans(batch.spans)
    existing_trace.cost_cents = (existing_trace.cost_cents or 0.0) + cost

    return accepted


def _estimate_response_latency(spans) -> Optional[float]:
    """Quick pre-sampling estimate from spans; used only for tail-sampling gate."""
    vad_stop: Optional[float] = None
    tts_start: Optional[float] = None

    for s in spans:
        if s.component == "vad" and s.fields:
            vad_stop = s.fields.get("speech_stop_ms")
        if s.component == "tts" and tts_start is None:
            tts_start = s.start_ms

    if vad_stop is not None and tts_start is not None:
        return max(0.0, tts_start - vad_stop)
    return None


def _extract_cost_from_spans(spans) -> float:
    total = 0.0
    for s in spans:
        if s.fields:
            c = s.fields.get("cost_cents")
            if c:
                total += float(c)
    return total


async def _compute_turn_latency(session: AsyncSession, turn_id: str) -> None:
    """
    Compute response_latency_ms and ttfb_ms for a turn from its spans.
    Stores result on the Turn row.
    §4 formula:
      response_latency_ms = first_tts_start_ms - vad_speech_stop_ms
      ttfb_ms = TTS span ttfb_ms
    Fallback: if VAD absent, use turn.started_at offset as 0ms.
    """
    turn = await session.get(Turn, turn_id)
    if turn is None:
        return

    # If the SDK supplied an explicit response latency, trust it and skip the
    # span-derived estimate (which needs VAD/TTS span timings we may not have).
    if turn.response_latency_ms is not None:
        return

    # Load all spans for this turn
    result = await session.execute(
        select(Span).where(Span.turn_id == turn_id)
    )
    spans = list(result.scalars().all())

    if not spans:
        return

    # Find VAD stop
    vad_stop_ms: Optional[float] = None
    for sp in spans:
        if sp.component == ComponentType.vad and sp.fields:
            vad_stop_ms = sp.fields.get("speech_stop_ms")
            if vad_stop_ms is not None:
                break

    # Find first TTS span start
    tts_spans = [sp for sp in spans if sp.component == ComponentType.tts]
    tts_spans.sort(key=lambda s: s.start_ms)
    first_tts_start_ms: Optional[float] = tts_spans[0].start_ms if tts_spans else None

    # Compute response_latency_ms
    if vad_stop_ms is not None and first_tts_start_ms is not None:
        latency = max(0.0, first_tts_start_ms - vad_stop_ms)
        turn.response_latency_ms = latency
    elif first_tts_start_ms is not None:
        # Fallback: no VAD span — use tts start_ms as offset (turn start = 0)
        # §9.5: set latency_basis flag in trace.meta when reconciling
        turn.response_latency_ms = first_tts_start_ms
    # else: can't compute

    # TTS ttfb_ms
    if tts_spans and tts_spans[0].ttfb_ms is not None:
        turn.ttfb_ms = tts_spans[0].ttfb_ms
    elif tts_spans and tts_spans[0].fields:
        turn.ttfb_ms = tts_spans[0].fields.get("ttfb_ms")


async def _compute_trace_percentiles(session: AsyncSession, trace_id: str) -> None:
    """Compute p50/p95/p99 of response_latency_ms over turns; store on trace."""
    trace = await session.get(Trace, trace_id)
    if trace is None:
        return

    result = await session.execute(
        select(Turn.response_latency_ms).where(
            Turn.trace_id == trace_id,
            Turn.response_latency_ms.isnot(None),
        )
    )
    latencies = [row[0] for row in result.all()]

    if not latencies:
        return

    sorted_lat = sorted(latencies)
    n = len(sorted_lat)

    def percentile(p: float) -> float:
        idx = int(p / 100 * n)
        idx = min(idx, n - 1)
        return sorted_lat[idx]

    trace.e2e_p50_ms = percentile(50)
    trace.e2e_p95_ms = percentile(95)
    trace.e2e_p99_ms = percentile(99)

    if trace.ended_at and trace.started_at:
        delta = trace.ended_at - trace.started_at
        trace.duration_ms = int(delta.total_seconds() * 1000)
