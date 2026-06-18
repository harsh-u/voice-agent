import uuid
import enum
from datetime import datetime, UTC
from typing import Optional, Any

from sqlalchemy import (
    String, Text, ForeignKey, Enum as SAEnum, Integer, Float, DateTime,
    Boolean, Index, UniqueConstraint, MetaData,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, relationship


class UTCDateTime(TypeDecorator):
    """DateTime that always returns timezone-aware UTC datetimes from the DB."""

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if value is not None and value.tzinfo is not None:
            return value.astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value, dialect):
        if value is not None and value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    # Unified platform: observability tables live in the Postgres "obs" schema so
    # they share one database with the voiceagent (public) and voicerag (rag)
    # tables without name collisions (e.g. users, api_keys).
    metadata = MetaData(schema="obs")


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class ComponentType(str, enum.Enum):
    vad = "vad"
    stt = "stt"
    llm = "llm"
    tts = "tts"
    transport = "transport"
    telephony = "telephony"


class TraceStatus(str, enum.Enum):
    active = "active"
    completed = "completed"
    error = "error"


class TurnRole(str, enum.Enum):
    user = "user"
    agent = "agent"


# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    projects: Mapped[list["Project"]] = relationship("Project", back_populates="user")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Per-project sampling config
    sample_rate: Mapped[float] = mapped_column(Float, nullable=False, default=1.0)
    slow_threshold_ms: Mapped[float] = mapped_column(Float, nullable=False, default=800.0)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="projects")
    api_keys: Mapped[list["ApiKey"]] = relationship("ApiKey", back_populates="project")
    traces: Mapped[list["Trace"]] = relationship("Trace", back_populates="project")


class ApiKey(Base):
    __tablename__ = "api_keys"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, index=True
    )
    key_prefix: Mapped[str] = mapped_column(String(16), nullable=False)
    hashed_key: Mapped[str] = mapped_column(String(255), nullable=False, index=True)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_used_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="api_keys")


class Trace(Base):
    """One call / session."""
    __tablename__ = "traces"

    # 64 chars: trace IDs may be externally-supplied composite IDs (e.g. a call id).
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    external_call_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    framework: Mapped[str] = mapped_column(String(32), nullable=False, default="custom")
    status: Mapped[str] = mapped_column(
        SAEnum(TraceStatus, name="trace_status"), nullable=False, default=TraceStatus.active
    )
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, index=True,
        default=lambda: datetime.now(UTC)
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    duration_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    # Per-trace response-latency percentiles over turns
    e2e_p50_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    e2e_p95_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    e2e_p99_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    turn_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cents: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    sampled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    meta: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    project: Mapped["Project"] = relationship("Project", back_populates="traces")
    turns: Mapped[list["Turn"]] = relationship("Turn", back_populates="trace")
    spans: Mapped[list["Span"]] = relationship("Span", back_populates="trace")


class Turn(Base):
    """One user<->agent exchange within a trace."""
    __tablename__ = "turns"

    # 64 chars: turn IDs are composite (e.g. "<call_id>-t0").
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    trace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("traces.id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    turn_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    role: Mapped[str] = mapped_column(
        SAEnum(TurnRole, name="turn_role"), nullable=False, default=TurnRole.agent
    )
    user_transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    agent_transcript: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    # Hero metric: wall time from end-of-user-speech to first audible agent byte
    response_latency_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ttfb_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    interrupted: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    dead_air_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime, nullable=False, index=True,
        default=lambda: datetime.now(UTC)
    )
    ended_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    trace: Mapped["Trace"] = relationship("Trace", back_populates="turns")
    spans: Mapped[list["Span"]] = relationship("Span", back_populates="turn")


class Span(Base):
    """One component activation within a turn."""
    __tablename__ = "spans"

    # 64 chars: span IDs are composite (e.g. "<call_id>-t0-stt").
    id: Mapped[str] = mapped_column(
        String(64), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    turn_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("turns.id"), nullable=False, index=True
    )
    trace_id: Mapped[str] = mapped_column(
        String(64), ForeignKey("traces.id"), nullable=False, index=True
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    component: Mapped[str] = mapped_column(
        SAEnum(ComponentType, name="component_type"), nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    start_ms: Mapped[float] = mapped_column(Float, nullable=False)
    end_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    duration_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    ttfb_ms: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fields: Mapped[Optional[Any]] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    turn: Mapped["Turn"] = relationship("Turn", back_populates="spans")
    trace: Mapped["Trace"] = relationship("Trace", back_populates="spans")

    __table_args__ = (
        Index("ix_spans_turn_component", "turn_id", "component"),
        UniqueConstraint("project_id", "id", name="uq_spans_project_id"),
    )


class MetricRollup(Base):
    """Pre-aggregated per-component percentile rows (5-min buckets)."""
    __tablename__ = "metric_rollups"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: uuid.uuid4().hex
    )
    project_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("projects.id"), nullable=False, index=True
    )
    component: Mapped[Optional[str]] = mapped_column(
        SAEnum(ComponentType, name="component_type"),
        nullable=True,
        # null = end-to-end response latency
    )
    window_start: Mapped[datetime] = mapped_column(UTCDateTime, nullable=False, index=True)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False, default=300)
    count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    p50_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    p95_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    p99_ms: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    error_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    cost_cents: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
