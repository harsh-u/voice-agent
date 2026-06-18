"""
Per-component p50/p95/p99 aggregation over 5-minute time buckets.

Writes rows into metric_rollups. Called by the background rollup task every 60s.
"""
from __future__ import annotations
import logging
import statistics
from datetime import datetime, UTC, timedelta

from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from voxscope.db.models import Span, Turn, MetricRollup, ComponentType
from voxscope.db.session import AsyncSessionLocal
from voxscope.config import settings

logger = logging.getLogger(__name__)

BUCKET_SECONDS = 300  # 5-minute windows


def _floor_to_bucket(dt: datetime, bucket_secs: int = BUCKET_SECONDS) -> datetime:
    """Floor a UTC datetime down to the nearest bucket boundary."""
    ts = int(dt.timestamp())
    floored = (ts // bucket_secs) * bucket_secs
    return datetime.fromtimestamp(floored, tz=UTC)


def _compute_percentiles(values: list[float]) -> tuple[float, float, float]:
    """Return (p50, p95, p99) for a list of floats. Requires at least 1 element."""
    if not values:
        return 0.0, 0.0, 0.0
    s = sorted(values)
    n = len(s)

    def pct(p: float) -> float:
        idx = int(p / 100.0 * n)
        return s[min(idx, n - 1)]

    return pct(50), pct(95), pct(99)


def aggregate_rollups(rows) -> list[dict]:
    """Collapse per-bucket rollup rows into ONE per-component aggregate (§8 contract).

    Each row must expose ``component, count, p50_ms, p95_ms, p99_ms, error_count,
    cost_cents``. Counts / errors / cost are exact sums; percentiles are
    count-weighted across the window's buckets (an approximation — exact
    percentiles cannot be recomposed from per-bucket percentiles). Result is
    sorted end-to-end (``component is None``) first, then alphabetical by component.
    Pure function — no DB access — so it is unit-testable in isolation.
    """
    by_component: dict = {}
    for r in rows:
        by_component.setdefault(r.component, []).append(r)

    out: list[dict] = []
    for comp, group in by_component.items():
        total = sum(r.count for r in group)

        def _weighted(attr: str, _group=group, _total=total) -> float:
            if _total == 0:
                return 0.0
            return sum(getattr(r, attr) * r.count for r in _group) / _total

        out.append(
            {
                "component": comp,
                "count": total,
                "p50_ms": round(_weighted("p50_ms"), 2),
                "p95_ms": round(_weighted("p95_ms"), 2),
                "p99_ms": round(_weighted("p99_ms"), 2),
                "error_count": sum(r.error_count for r in group),
                "cost_cents": round(sum(r.cost_cents for r in group), 4),
            }
        )

    out.sort(key=lambda m: (m["component"] is not None, m["component"] or ""))
    return out


async def run_rollup() -> None:
    """
    Aggregate per-component span durations and per-trace response latencies
    over 5-minute buckets. Computes p50/p95/p99 and writes/updates metric_rollups rows.

    Covers the last two complete 5-min buckets (to handle late arrivals) and the
    current in-progress bucket.
    """
    now = datetime.now(UTC)
    # Process the past 3 buckets (current + 2 prior) to handle late spans
    cutoff = _floor_to_bucket(now) - timedelta(seconds=2 * BUCKET_SECONDS)

    async with AsyncSessionLocal() as session:
        try:
            await _rollup_span_components(session, cutoff)
            await _rollup_e2e_latency(session, cutoff)
            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error(f"[rollup] aggregation failed: {exc}", exc_info=True)


async def _rollup_span_components(session: AsyncSession, since: datetime) -> None:
    """Aggregate span duration_ms per project + component into metric_rollups."""
    # Fetch spans created since the window start
    result = await session.execute(
        select(Span).where(
            Span.created_at >= since,
            Span.duration_ms.isnot(None),
        )
    )
    spans = list(result.scalars().all())

    if not spans:
        return

    # Group by (project_id, component, window_start)
    buckets: dict[tuple[str, str, datetime], list] = {}
    for span in spans:
        window = _floor_to_bucket(span.created_at)
        key = (span.project_id, span.component, window)
        if key not in buckets:
            buckets[key] = {"durations": [], "errors": 0, "cost": 0.0}
        entry = buckets[key]
        entry["durations"].append(span.duration_ms)
        if span.error:
            entry["errors"] += 1
        if span.fields and span.fields.get("cost_cents"):
            entry["cost"] += float(span.fields["cost_cents"])

    for (project_id, component, window_start), data in buckets.items():
        p50, p95, p99 = _compute_percentiles(data["durations"])

        # Check if a rollup row for this exact window already exists
        existing = await session.execute(
            select(MetricRollup).where(
                MetricRollup.project_id == project_id,
                MetricRollup.component == component,
                MetricRollup.window_start == window_start,
                MetricRollup.window_seconds == BUCKET_SECONDS,
            )
        )
        row = existing.scalar_one_or_none()

        if row is not None:
            row.count = len(data["durations"])
            row.p50_ms = p50
            row.p95_ms = p95
            row.p99_ms = p99
            row.error_count = data["errors"]
            row.cost_cents = data["cost"]
        else:
            row = MetricRollup(
                project_id=project_id,
                component=component,
                window_start=window_start,
                window_seconds=BUCKET_SECONDS,
                count=len(data["durations"]),
                p50_ms=p50,
                p95_ms=p95,
                p99_ms=p99,
                error_count=data["errors"],
                cost_cents=data["cost"],
            )
            session.add(row)


async def _rollup_e2e_latency(session: AsyncSession, since: datetime) -> None:
    """
    Aggregate end-to-end response_latency_ms per project into metric_rollups
    with component=NULL (meaning end-to-end).
    """
    result = await session.execute(
        select(Turn).where(
            Turn.created_at >= since,
            Turn.response_latency_ms.isnot(None),
        )
    )
    turns = list(result.scalars().all())

    if not turns:
        return

    buckets: dict[tuple[str, datetime], list] = {}
    for turn in turns:
        window = _floor_to_bucket(turn.created_at)
        key = (turn.project_id, window)
        if key not in buckets:
            buckets[key] = []
        buckets[key].append(turn.response_latency_ms)

    for (project_id, window_start), latencies in buckets.items():
        p50, p95, p99 = _compute_percentiles(latencies)

        existing = await session.execute(
            select(MetricRollup).where(
                MetricRollup.project_id == project_id,
                MetricRollup.component.is_(None),
                MetricRollup.window_start == window_start,
                MetricRollup.window_seconds == BUCKET_SECONDS,
            )
        )
        row = existing.scalar_one_or_none()

        if row is not None:
            row.count = len(latencies)
            row.p50_ms = p50
            row.p95_ms = p95
            row.p99_ms = p99
        else:
            row = MetricRollup(
                project_id=project_id,
                component=None,
                window_start=window_start,
                window_seconds=BUCKET_SECONDS,
                count=len(latencies),
                p50_ms=p50,
                p95_ms=p95,
                p99_ms=p99,
                error_count=0,
                cost_cents=0.0,
            )
            session.add(row)


async def run_retention() -> None:
    """
    Retention / reaper task (§5.5 + §9.6):
    1. Delete raw spans older than raw_span_retention_days.
    2. Delete metric_rollups older than rollup_retention_days.
    3. Close idle/open traces with no activity for > trace_idle_seconds.
    """
    from voxscope.db.models import Trace, TraceStatus

    now = datetime.now(UTC)
    raw_cutoff = now - timedelta(days=settings.raw_span_retention_days)
    rollup_cutoff = now - timedelta(days=settings.rollup_retention_days)
    idle_cutoff = now - timedelta(seconds=settings.trace_idle_seconds)

    async with AsyncSessionLocal() as session:
        try:
            # Delete old raw spans
            await session.execute(
                delete(Span).where(Span.created_at < raw_cutoff)
            )

            # Delete old rollups
            await session.execute(
                delete(MetricRollup).where(MetricRollup.window_start < rollup_cutoff)
            )

            # Close idle open traces (status=active, ended_at=null, no recent spans)
            idle_result = await session.execute(
                select(Trace).where(
                    Trace.status == TraceStatus.active,
                    Trace.ended_at.is_(None),
                    Trace.created_at < idle_cutoff,
                )
            )
            idle_traces = idle_result.scalars().all()

            for trace in idle_traces:
                # Check if any span was recently created for this trace
                recent_span = await session.execute(
                    select(Span).where(
                        Span.trace_id == trace.id,
                        Span.created_at >= idle_cutoff,
                    ).limit(1)
                )
                if recent_span.scalar_one_or_none() is None:
                    trace.status = TraceStatus.error
                    trace.ended_at = now
                    logger.info(f"[reaper] closed idle trace {trace.id}")

            await session.commit()
        except Exception as exc:
            await session.rollback()
            logger.error(f"[retention] task failed: {exc}", exc_info=True)
