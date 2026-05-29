import uuid
from datetime import datetime, UTC
from typing import Optional

from sqlalchemy import String, Text, ForeignKey, Enum as SAEnum, Integer, Float, DateTime
from sqlalchemy.types import TypeDecorator
from sqlalchemy.orm import DeclarativeBase, mapped_column, Mapped, relationship
import enum


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
    pass


class CallDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class CallStatus(str, enum.Enum):
    dialing = "dialing"
    active = "active"
    completed = "completed"
    failed = "failed"


class AgentConfig(Base):
    __tablename__ = "agent_configs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    system_prompt: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    voice_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    llm_model: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    tools_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    sip_trunk_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    calls: Mapped[list["Call"]] = relationship("Call", back_populates="agent_config")


class Call(Base):
    __tablename__ = "calls"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    agent_config_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("agent_configs.id"), nullable=True
    )
    direction: Mapped[str] = mapped_column(
        SAEnum(CallDirection, name="call_direction"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        SAEnum(CallStatus, name="call_status"),
        nullable=False,
        default=CallStatus.dialing,
    )
    from_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    to_number: Mapped[Optional[str]] = mapped_column(String(50), nullable=True)
    livekit_room_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    ended_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    duration_seconds: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    cost_cents: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    agent_config: Mapped[Optional["AgentConfig"]] = relationship(
        "AgentConfig", back_populates="calls"
    )
    turns: Mapped[list["TranscriptTurn"]] = relationship(
        "TranscriptTurn", back_populates="call"
    )


class TranscriptTurn(Base):
    __tablename__ = "transcript_turns"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    call_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("calls.id"), nullable=False
    )
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    text: Mapped[str] = mapped_column(Text, nullable=False)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    latency_ms: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    call: Mapped["Call"] = relationship("Call", back_populates="turns")
