"""Aggregation response schemas."""
from __future__ import annotations
from datetime import datetime
from typing import Optional
from pydantic import BaseModel


class ComponentLatencyMetric(BaseModel):
    """A single pre-aggregated rollup bucket row (per-component, per 5-min window)."""
    component: Optional[str]  # null = end-to-end
    window_start: datetime
    window_seconds: int
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    error_count: int
    cost_cents: float

    model_config = {"from_attributes": True}


class AggregatedLatencyMetric(BaseModel):
    """Per-component aggregate over the requested window (the §8 contract).

    Percentiles are count-weighted across the window's buckets — an approximation,
    since exact percentiles can't be recomposed from bucket percentiles. Counts,
    errors and cost are exact sums.
    """
    component: Optional[str]  # null = end-to-end (turn response latency)
    count: int
    p50_ms: float
    p95_ms: float
    p99_ms: float
    error_count: int
    cost_cents: float


class LatencyMetricsResponse(BaseModel):
    metrics: list[AggregatedLatencyMetric]
