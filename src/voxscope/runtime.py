"""Background tasks for the observability engine, extracted so they can run
inside the unified voice-agent app (and the standalone VoxScope app).

* ``drain_ingest_queue`` — drains the in-process ingest queue, bulk-writing
  traces/turns/spans to Postgres.
* ``rollup_task`` — periodic metric pre-aggregation (p50/p95/p99 buckets).
* ``retention_task`` — periodic retention / reaper (drops old spans + rollups,
  closes idle traces).
"""
import asyncio
import logging

from voxscope.config import settings

logger = logging.getLogger(__name__)


async def drain_ingest_queue(queue: asyncio.Queue) -> None:
    from voxscope.ingestion.writer import process_batch

    logger.info("[drain] ingest queue drain task started")
    while True:
        item = await queue.get()
        if item is None:
            queue.task_done()
            break
        try:
            count = await process_batch(
                batch=item["batch"],
                project_id=item["project_id"],
                sample_rate=item["sample_rate"],
                slow_threshold_ms=item["slow_threshold_ms"],
            )
            logger.debug("[drain] wrote %d spans for project %s", count, item["project_id"])
        except Exception as exc:
            logger.error("[drain] error processing batch: %s", exc, exc_info=True)
        finally:
            queue.task_done()


async def rollup_task() -> None:
    from voxscope.aggregation.percentiles import run_rollup

    logger.info("[rollup] metric rollup task started (interval=%ds)", settings.rollup_interval_seconds)
    while True:
        await asyncio.sleep(settings.rollup_interval_seconds)
        try:
            await run_rollup()
        except Exception as exc:
            logger.error("[rollup] error: %s", exc, exc_info=True)


async def retention_task() -> None:
    from voxscope.aggregation.percentiles import run_retention

    logger.info("[retention] retention task started (interval=%ds)", settings.retention_interval_seconds)
    while True:
        await asyncio.sleep(settings.retention_interval_seconds)
        try:
            await run_retention()
        except Exception as exc:
            logger.error("[retention] error: %s", exc, exc_info=True)
