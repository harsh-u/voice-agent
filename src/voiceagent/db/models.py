import uuid
from datetime import datetime, UTC
from typing import Optional

from sqlalchemy import (
    String, Text, ForeignKey, Enum as SAEnum, Integer, Float, DateTime,
    Boolean, JSON, BigInteger, UniqueConstraint, Index,
)
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


# ---------------------------------------------------------------------------
# Enums
# ---------------------------------------------------------------------------

class CallDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class CallStatus(str, enum.Enum):
    dialing = "dialing"
    active = "active"
    completed = "completed"
    failed = "failed"


class ConversationStatus(str, enum.Enum):
    open = "open"
    pending = "pending"
    closed = "closed"


class MessageDirection(str, enum.Enum):
    inbound = "inbound"
    outbound = "outbound"


class MessageType(str, enum.Enum):
    text = "text"
    image = "image"
    video = "video"
    audio = "audio"
    document = "document"
    location = "location"
    template = "template"
    interactive = "interactive"
    note = "note"  # internal CRM note (e.g. post-call summary)


class MessageStatus(str, enum.Enum):
    pending = "pending"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"


class TemplateStatus(str, enum.Enum):
    pending = "pending"
    approved = "approved"
    rejected = "rejected"


class BroadcastStatus(str, enum.Enum):
    draft = "draft"
    scheduled = "scheduled"
    sending = "sending"
    completed = "completed"
    failed = "failed"


class DealStatus(str, enum.Enum):
    open = "open"
    won = "won"
    lost = "lost"


class UserRole(str, enum.Enum):
    admin = "admin"
    agent = "agent"


# ---------------------------------------------------------------------------
# ── CRM: Users & Auth ───────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    hashed_password: Mapped[str] = mapped_column(String(255), nullable=False)
    full_name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    avatar_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    role: Mapped[str] = mapped_column(
        SAEnum(UserRole, name="user_role"), nullable=False, default=UserRole.agent
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    whatsapp_config: Mapped[Optional["WhatsAppConfig"]] = relationship(
        "WhatsAppConfig", back_populates="user", uselist=False
    )
    contacts: Mapped[list["Contact"]] = relationship("Contact", back_populates="user")
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation", back_populates="assignee",
        foreign_keys="Conversation.assigned_to",
    )
    pipelines: Mapped[list["Pipeline"]] = relationship("Pipeline", back_populates="user")
    automations: Mapped[list["Automation"]] = relationship("Automation", back_populates="user")
    flows: Mapped[list["Flow"]] = relationship("Flow", back_populates="user")


# ---------------------------------------------------------------------------
# ── CRM: WhatsApp Config ────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class WhatsAppConfig(Base):
    __tablename__ = "whatsapp_configs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False, unique=True
    )
    phone_number_id: Mapped[str] = mapped_column(String(64), nullable=False)
    waba_id: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    # AES-256-GCM encrypted access token
    access_token_enc: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    webhook_verify_token: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    app_secret: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", back_populates="whatsapp_config")


# ---------------------------------------------------------------------------
# ── CRM: Contacts & Tags ────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    __table_args__ = (UniqueConstraint("user_id", "name", name="uq_tag_user_name"),)

    contacts: Mapped[list["Contact"]] = relationship(
        "Contact", secondary="contact_tags", back_populates="tags"
    )


class ContactTag(Base):
    __tablename__ = "contact_tags"

    contact_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contacts.id", ondelete="CASCADE"), primary_key=True
    )
    tag_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True
    )


class Contact(Base):
    __tablename__ = "contacts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    name: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    email: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    company: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    # Stored as JSON: {"field_key": "value", ...}
    custom_fields: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint("user_id", "phone", name="uq_contact_user_phone"),
        Index("ix_contacts_user_id", "user_id"),
    )

    user: Mapped["User"] = relationship("User", back_populates="contacts")
    tags: Mapped[list["Tag"]] = relationship(
        "Tag", secondary="contact_tags", back_populates="contacts"
    )
    conversations: Mapped[list["Conversation"]] = relationship(
        "Conversation", back_populates="contact"
    )
    deals: Mapped[list["Deal"]] = relationship("Deal", back_populates="contact")
    calls: Mapped[list["Call"]] = relationship("Call", back_populates="contact")


# ---------------------------------------------------------------------------
# ── CRM: Conversations & Messages ───────────────────────────────────────────
# ---------------------------------------------------------------------------

class Conversation(Base):
    __tablename__ = "conversations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    contact_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contacts.id"), nullable=False
    )
    # The workspace owner (for multi-user: which account this belongs to)
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    assigned_to: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(
        SAEnum(ConversationStatus, name="conversation_status"),
        nullable=False,
        default=ConversationStatus.open,
    )
    last_message_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (Index("ix_conversations_contact_id", "contact_id"),)

    contact: Mapped["Contact"] = relationship("Contact", back_populates="conversations")
    assignee: Mapped[Optional["User"]] = relationship(
        "User", back_populates="conversations", foreign_keys=[assigned_to]
    )
    messages: Mapped[list["Message"]] = relationship(
        "Message", back_populates="conversation", order_by="Message.created_at"
    )


class Message(Base):
    __tablename__ = "messages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    conversation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("conversations.id"), nullable=False
    )
    direction: Mapped[str] = mapped_column(
        SAEnum(MessageDirection, name="message_direction"), nullable=False
    )
    type: Mapped[str] = mapped_column(
        SAEnum(MessageType, name="message_type"), nullable=False, default=MessageType.text
    )
    # Flexible content: {"text": "..."} or {"url": "...", "caption": "..."} etc.
    content: Mapped[dict] = mapped_column(JSON, nullable=False, default=dict)
    # WhatsApp message ID from Meta (for delivery tracking)
    wa_message_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True, unique=True)
    status: Mapped[str] = mapped_column(
        SAEnum(MessageStatus, name="message_status"),
        nullable=False,
        default=MessageStatus.pending,
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (Index("ix_messages_conversation_id", "conversation_id"),)

    conversation: Mapped["Conversation"] = relationship(
        "Conversation", back_populates="messages"
    )


# ---------------------------------------------------------------------------
# ── CRM: Message Templates ──────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class MessageTemplate(Base):
    __tablename__ = "message_templates"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="en_US")
    category: Mapped[str] = mapped_column(String(64), nullable=False)
    # Meta template components JSON
    components: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    status: Mapped[str] = mapped_column(
        SAEnum(TemplateStatus, name="template_status"),
        nullable=False,
        default=TemplateStatus.pending,
    )
    wa_template_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )


# ---------------------------------------------------------------------------
# ── CRM: Sales Pipeline ─────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class Pipeline(Base):
    __tablename__ = "pipelines"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    user: Mapped["User"] = relationship("User", back_populates="pipelines")
    stages: Mapped[list["PipelineStage"]] = relationship(
        "PipelineStage", back_populates="pipeline", order_by="PipelineStage.position"
    )


class PipelineStage(Base):
    __tablename__ = "pipeline_stages"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    pipeline_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pipelines.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(128), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    color: Mapped[Optional[str]] = mapped_column(String(16), nullable=True)

    pipeline: Mapped["Pipeline"] = relationship("Pipeline", back_populates="stages")
    deals: Mapped[list["Deal"]] = relationship("Deal", back_populates="stage")


class Deal(Base):
    __tablename__ = "deals"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    pipeline_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pipelines.id"), nullable=False
    )
    stage_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("pipeline_stages.id"), nullable=False
    )
    contact_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("contacts.id"), nullable=True
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    value: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    close_date: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    status: Mapped[str] = mapped_column(
        SAEnum(DealStatus, name="deal_status"), nullable=False, default=DealStatus.open
    )
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    stage: Mapped["PipelineStage"] = relationship("PipelineStage", back_populates="deals")
    contact: Mapped[Optional["Contact"]] = relationship("Contact", back_populates="deals")


# ---------------------------------------------------------------------------
# ── CRM: Broadcasts ─────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class Broadcast(Base):
    __tablename__ = "broadcasts"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    template_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("message_templates.id"), nullable=True
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(
        SAEnum(BroadcastStatus, name="broadcast_status"),
        nullable=False,
        default=BroadcastStatus.draft,
    )
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    sent_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    failed_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    recipients: Mapped[list["BroadcastRecipient"]] = relationship(
        "BroadcastRecipient", back_populates="broadcast"
    )


class BroadcastRecipient(Base):
    __tablename__ = "broadcast_recipients"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    broadcast_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("broadcasts.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contacts.id"), nullable=False
    )
    status: Mapped[str] = mapped_column(
        SAEnum(MessageStatus, name="broadcast_recipient_status"),
        nullable=False,
        default=MessageStatus.pending,
    )
    sent_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    broadcast: Mapped["Broadcast"] = relationship("Broadcast", back_populates="recipients")
    contact: Mapped["Contact"] = relationship("Contact")


# ---------------------------------------------------------------------------
# ── CRM: Automations ────────────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class Automation(Base):
    __tablename__ = "automations"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    trigger_type: Mapped[str] = mapped_column(String(64), nullable=False)
    trigger_config: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    steps: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", back_populates="automations")
    logs: Mapped[list["AutomationLog"]] = relationship(
        "AutomationLog", back_populates="automation"
    )


class AutomationLog(Base):
    __tablename__ = "automation_logs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    automation_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("automations.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("contacts.id"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False, default="running")
    # {"steps": [{"id": "...", "result": "...", "error": null}]}
    steps_results: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    # For delay/wait steps — resume after this time
    resume_at: Mapped[Optional[datetime]] = mapped_column(UTCDateTime, nullable=True)
    current_step_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    automation: Mapped["Automation"] = relationship("Automation", back_populates="logs")


# ---------------------------------------------------------------------------
# ── CRM: Conversation Flows ─────────────────────────────────────────────────
# ---------------------------------------------------------------------------

class Flow(Base):
    __tablename__ = "flows"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    user_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("users.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    # Node graph: {"nodes": [...], "edges": [...]}
    nodes: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    edges: Mapped[Optional[list]] = mapped_column(JSON, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime,
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
        nullable=False,
    )

    user: Mapped["User"] = relationship("User", back_populates="flows")
    runs: Mapped[list["FlowRun"]] = relationship("FlowRun", back_populates="flow")


class FlowRun(Base):
    __tablename__ = "flow_runs"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    flow_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("flows.id", ondelete="CASCADE"), nullable=False
    )
    contact_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("contacts.id"), nullable=False
    )
    current_node_id: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    # Arbitrary state for the current node (collected inputs, vars, etc.)
    state: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    is_complete: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    started_at: Mapped[datetime] = mapped_column(
        UTCDateTime, default=lambda: datetime.now(UTC), nullable=False
    )

    __table_args__ = (
        # One active run per contact per flow
        Index("ix_flow_runs_contact_flow", "contact_id", "flow_id"),
    )

    flow: Mapped["Flow"] = relationship("Flow", back_populates="runs")
    contact: Mapped["Contact"] = relationship("Contact")


# ---------------------------------------------------------------------------
# ── Voice: AgentConfig, Call, TranscriptTurn ────────────────────────────────
# ---------------------------------------------------------------------------

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
    rag_api_key: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    rag_kb_id: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
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
    # Link to CRM contact (set when call is initiated from CRM)
    contact_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("contacts.id"), nullable=True
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
    recording_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)

    agent_config: Mapped[Optional["AgentConfig"]] = relationship(
        "AgentConfig", back_populates="calls"
    )
    contact: Mapped[Optional["Contact"]] = relationship("Contact", back_populates="calls")
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
