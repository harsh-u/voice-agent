"""Query/response schemas for traces and turns."""
from __future__ import annotations
from datetime import datetime
from typing import Any, Optional
from pydantic import BaseModel, EmailStr
from voxscope.schemas.span import SpanResponse


class TurnResponse(BaseModel):
    id: str
    trace_id: str
    project_id: str
    turn_index: int
    role: str
    user_transcript: Optional[str]
    agent_transcript: Optional[str]
    response_latency_ms: Optional[float]
    ttfb_ms: Optional[float]
    interrupted: bool
    dead_air_ms: Optional[float]
    started_at: datetime
    ended_at: Optional[datetime]
    spans: list[SpanResponse] = []

    model_config = {"from_attributes": True}


class TraceResponse(BaseModel):
    id: str
    project_id: str
    external_call_id: Optional[str]
    framework: str
    status: str
    started_at: datetime
    ended_at: Optional[datetime]
    duration_ms: Optional[int]
    e2e_p50_ms: Optional[float]
    e2e_p95_ms: Optional[float]
    e2e_p99_ms: Optional[float]
    turn_count: int
    cost_cents: float
    sampled: bool
    meta: Optional[Any]
    created_at: datetime

    model_config = {"from_attributes": True}


class TraceDetailResponse(TraceResponse):
    """Full waterfall payload: trace + turns + spans."""
    turns: list[TurnResponse] = []


class TraceListResponse(BaseModel):
    traces: list[TraceResponse]
    total_count: int


# Auth schemas (shared between auth.py and projects.py)
class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
