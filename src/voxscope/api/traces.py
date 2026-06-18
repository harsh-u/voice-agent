"""
GET /v1/traces         — paginated trace list
GET /v1/traces/{id}    — full waterfall payload (trace + turns + spans)
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from voxscope.core.security import get_current_user
from voxscope.db.models import Project, Span, Trace, Turn, User
from voxscope.db.session import get_session
from voxscope.schemas.trace import TraceDetailResponse, TraceListResponse, TraceResponse, TurnResponse
from voxscope.schemas.span import SpanResponse

router = APIRouter(prefix="/v1/traces", tags=["traces"])


def _assert_project_owned(project: Project, current_user: User) -> None:
    if project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")


@router.get("", response_model=TraceListResponse)
async def list_traces(
    project_id: str = Query(..., description="Project ID to filter traces"),
    framework: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    page: int = Query(1, ge=1),
    limit: int = Query(50, ge=1, le=200),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # Verify project ownership
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    _assert_project_owned(project, current_user)

    # Build query
    q = select(Trace).where(Trace.project_id == project_id)
    if framework:
        q = q.where(Trace.framework == framework)
    if status:
        q = q.where(Trace.status == status)
    if from_:
        q = q.where(Trace.started_at >= from_)
    if to:
        q = q.where(Trace.started_at <= to)

    # Count total
    count_q = select(func.count()).select_from(q.subquery())
    total_result = await session.execute(count_q)
    total_count = total_result.scalar() or 0

    # Paginate
    offset = (page - 1) * limit
    q = q.order_by(Trace.started_at.desc()).offset(offset).limit(limit)
    result = await session.execute(q)
    traces = result.scalars().all()

    return TraceListResponse(
        traces=[TraceResponse.model_validate(t) for t in traces],
        total_count=total_count,
    )


@router.get("/{trace_id}", response_model=TraceDetailResponse)
async def get_trace(
    trace_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    trace = await session.get(Trace, trace_id)
    if not trace:
        raise HTTPException(status_code=404, detail="Trace not found")

    project = await session.get(Project, trace.project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    _assert_project_owned(project, current_user)

    # Load turns for this trace
    turns_result = await session.execute(
        select(Turn).where(Turn.trace_id == trace_id).order_by(Turn.turn_index)
    )
    turns = list(turns_result.scalars().all())

    # Load all spans for this trace, grouped by turn_id
    spans_result = await session.execute(
        select(Span).where(Span.trace_id == trace_id).order_by(Span.start_ms)
    )
    all_spans = list(spans_result.scalars().all())

    # Map turn_id -> list of SpanResponse
    span_map: dict[str, list[SpanResponse]] = {}
    for span in all_spans:
        span_resp = SpanResponse.model_validate(span)
        span_map.setdefault(span.turn_id, []).append(span_resp)

    turn_responses = [
        TurnResponse(
            id=t.id,
            trace_id=t.trace_id,
            project_id=t.project_id,
            turn_index=t.turn_index,
            role=t.role,
            user_transcript=t.user_transcript,
            agent_transcript=t.agent_transcript,
            response_latency_ms=t.response_latency_ms,
            ttfb_ms=t.ttfb_ms,
            interrupted=t.interrupted,
            dead_air_ms=t.dead_air_ms,
            started_at=t.started_at,
            ended_at=t.ended_at,
            spans=span_map.get(t.id, []),
        )
        for t in turns
    ]

    trace_resp = TraceResponse.model_validate(trace)
    return TraceDetailResponse(
        **trace_resp.model_dump(),
        turns=turn_responses,
    )
