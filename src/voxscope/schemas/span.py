"""Per-component span response schemas."""
from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel


class SpanResponse(BaseModel):
    id: str
    turn_id: str
    trace_id: str
    project_id: str
    component: str
    name: str
    start_ms: float
    end_ms: Optional[float]
    duration_ms: Optional[float]
    ttfb_ms: Optional[float]
    error: Optional[str]
    fields: Optional[Any]
    created_at: datetime

    model_config = {"from_attributes": True}
