"""
POST /v1/ingest/batch — the hot ingestion path.

Validates the batch, checks limits, enqueues to an in-process asyncio.Queue,
and returns 202 immediately. The background drain task does the actual DB writes.
"""
from __future__ import annotations
import asyncio
import logging
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request

from voxscope.config import settings
from voxscope.core.api_key import get_project_from_api_key
from voxscope.db.models import Project
from voxscope.schemas.ingest import IngestBatch, IngestResponse

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/v1/ingest", tags=["ingest"])


def get_ingest_queue(request: Request) -> asyncio.Queue:
    """Retrieve the app-level ingestion queue from app state."""
    return request.app.state.ingest_queue


@router.post("/batch", response_model=IngestResponse, status_code=202)
async def ingest_batch(
    body: IngestBatch,
    request: Request,
    project: Project = Depends(get_project_from_api_key),
):
    """
    Accepts a batch of spans/turns/traces for a project.

    - Returns 401 if the API key is invalid (handled by dependency).
    - Returns 413 if spans count exceeds the per-request limit.
    - Returns 202 with accepted count immediately; writes happen in background.
    """
    span_count = len(body.spans)

    # §9.8 — oversized batch check
    if span_count > settings.max_spans_per_batch:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Batch too large: {span_count} spans exceeds the limit of "
                f"{settings.max_spans_per_batch}. Reduce batch size and retry."
            ),
        )

    queue: asyncio.Queue = get_ingest_queue(request)

    # Enqueue the work item; the background drain task will pick it up.
    # Use put_nowait with a fallback to avoid blocking the HTTP handler.
    item = {
        "batch": body,
        "project_id": project.id,
        "sample_rate": project.sample_rate,
        "slow_threshold_ms": project.slow_threshold_ms,
    }
    try:
        queue.put_nowait(item)
    except asyncio.QueueFull:
        logger.warning("[ingest] queue full; dropping batch for project %s", project.id)
        # Still return 202 — telemetry must never fail the voice app
        return IngestResponse(accepted=0)

    return IngestResponse(accepted=span_count)
