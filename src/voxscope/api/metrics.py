"""
GET /v1/metrics/latency — per-component percentiles from metric_rollups.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voxscope.aggregation.percentiles import aggregate_rollups
from voxscope.core.security import get_current_user
from voxscope.db.models import MetricRollup, Project, User
from voxscope.db.session import get_session
from voxscope.schemas.metrics import AggregatedLatencyMetric, LatencyMetricsResponse

router = APIRouter(prefix="/v1/metrics", tags=["metrics"])


@router.get("/latency", response_model=LatencyMetricsResponse)
async def get_latency_metrics(
    project_id: str = Query(..., description="Project ID"),
    from_: Optional[datetime] = Query(None, alias="from"),
    to: Optional[datetime] = Query(None),
    component: Optional[str] = Query(None, description="Filter by component (e.g. stt, llm, tts). Omit for all."),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """
    Returns ONE per-component aggregate over the requested window (§8 contract):
    `{component, count, p50_ms, p95_ms, p99_ms, error_count, cost_cents}[]`.
    Reads pre-aggregated metric_rollups only — never scans raw spans. Percentiles are
    count-weighted across the window's buckets; counts/errors/cost are exact sums.
    """
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    q = select(MetricRollup).where(MetricRollup.project_id == project_id)

    if from_:
        q = q.where(MetricRollup.window_start >= from_)
    if to:
        q = q.where(MetricRollup.window_start <= to)
    if component:
        q = q.where(MetricRollup.component == component)

    result = await session.execute(q)
    rollups = result.scalars().all()

    # Collapse per-bucket rollup rows into one aggregate per component (pure, tested).
    metrics = [AggregatedLatencyMetric(**agg) for agg in aggregate_rollups(rollups)]
    return LatencyMetricsResponse(metrics=metrics)
