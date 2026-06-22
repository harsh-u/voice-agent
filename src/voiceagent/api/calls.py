import asyncio
import uuid
from datetime import datetime, UTC, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select, func
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession, AsyncSession as _AS
from loguru import logger

from voiceagent.db.models import (
    Call, CallDirection, CallStatus, AgentConfig, TranscriptTurn,
    Contact, Conversation, ConversationStatus, Message, MessageDirection,
    MessageType, MessageStatus,
)
from voiceagent.config import settings
from voiceagent.db.session import get_session, AsyncSessionLocal
from voiceagent.telephony.livekit_sip import create_room, dial_outbound
from voiceagent.agent.runner import run_agent

router = APIRouter(prefix="/calls", tags=["calls"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class OutboundCallRequest(BaseModel):
    to_number: str
    agent_config_id: Optional[str] = None
    contact_id: Optional[str] = None  # CRM contact to link this call to


class OutboundCallResponse(BaseModel):
    call_id: str
    room_name: str
    status: str


class TranscriptTurnResponse(BaseModel):
    id: str
    role: str
    text: str
    started_at: datetime
    latency_ms: Optional[int]

    model_config = {"from_attributes": True}


class CallSummary(BaseModel):
    id: str
    direction: str
    status: str
    from_number: Optional[str]
    to_number: Optional[str]
    livekit_room_name: Optional[str]
    started_at: Optional[datetime]
    ended_at: Optional[datetime]
    duration_seconds: Optional[int]
    cost_cents: Optional[float]
    recording_url: Optional[str] = None
    # Enriched fields (populated by list endpoints)
    agent_config_id: Optional[str] = None
    agent_name: Optional[str] = None
    contact_id: Optional[str] = None
    contact_name: Optional[str] = None

    model_config = {"from_attributes": True}


class ActiveCallDetail(BaseModel):
    """Live call card — shown on the monitoring widget."""
    id: str
    direction: str
    status: str
    to_number: Optional[str]
    from_number: Optional[str]
    started_at: Optional[datetime]
    live_duration_seconds: int
    estimated_cost_cents: float
    agent_name: Optional[str] = None
    contact_name: Optional[str] = None
    contact_id: Optional[str] = None


class CallDetail(CallSummary):
    turns: list[TranscriptTurnResponse]


class PaginatedCalls(BaseModel):
    total: int
    page: int
    limit: int
    items: list[CallSummary]


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/outbound", response_model=OutboundCallResponse, status_code=202)
async def create_outbound_call(
    body: OutboundCallRequest,
    session: AsyncSession = Depends(get_session),
):
    """Initiate an outbound call.

    Creates a LiveKit room, inserts a Call record, dials the SIP leg, then
    spawns a background asyncio task to run the voice pipeline.
    """
    # Validate agent config if provided, snapshot to a plain dict so the
    # background task doesn't reach back into a closed session.
    agent_cfg: dict | None = None
    if body.agent_config_id:
        ac = await session.get(AgentConfig, body.agent_config_id)
        if not ac:
            raise HTTPException(status_code=404, detail="Agent config not found")
        agent_cfg = {
            "id": ac.id,
            "system_prompt": ac.system_prompt,
            "voice_id": ac.voice_id,
            "llm_model": ac.llm_model,
            # Without these the pipeline can't enable RAG: the query_knowledge_base
            # tool is never registered, so the agent goes silent on KB questions.
            "rag_api_key": ac.rag_api_key,
            "rag_kb_id": ac.rag_kb_id,
        }

    room_name = f"call-{uuid.uuid4().hex[:12]}"
    call_id = str(uuid.uuid4())
    now = datetime.now(UTC)

    # Create LiveKit room
    try:
        await create_room(room_name)
    except Exception as exc:
        logger.error(f"Failed to create LiveKit room {room_name}: {exc}")
        raise HTTPException(status_code=502, detail=f"LiveKit room creation failed: {exc}")

    # Persist call record
    call = Call(
        id=call_id,
        agent_config_id=body.agent_config_id,
        contact_id=body.contact_id,
        direction=CallDirection.outbound,
        status=CallStatus.dialing,
        to_number=body.to_number,
        livekit_room_name=room_name,
        started_at=now,
    )
    session.add(call)
    await session.commit()

    # Run the voice pipeline as a background task — detached from this request.
    # The SIP leg is dialed from inside the pipeline once the bot has joined the
    # room (see run_pipeline.on_connected), so the phone only rings when the
    # agent is already present — this avoids the brief "ghost"/missed call that
    # happens when the callee answers before the bot is in the room.
    asyncio.create_task(run_agent(call_id, room_name, agent_cfg, to_number=body.to_number))
    logger.info(
        f"Outbound call {call_id} → {body.to_number} — pipeline task spawned "
        f"(agent_config_id={body.agent_config_id})"
    )

    return OutboundCallResponse(
        call_id=call_id,
        room_name=room_name,
        status=CallStatus.dialing,
    )


@router.get("/active", response_model=list[ActiveCallDetail])
async def list_active_calls(session: AsyncSession = Depends(get_session)):
    """Return all currently active or dialing calls — used for live monitoring."""
    result = await session.execute(
        select(Call)
        .where(Call.status.in_([CallStatus.active, CallStatus.dialing]))
        .options(selectinload(Call.agent_config), selectinload(Call.contact))
        .order_by(Call.started_at.asc())
    )
    calls = result.scalars().all()
    now = datetime.now(UTC)
    # Total cost per minute in dollars → convert to cents per second
    total_cpm = settings.cost_stt_cpm + settings.cost_llm_cpm + settings.cost_tts_cpm + settings.cost_telephony_cpm
    cost_per_sec = total_cpm / 60.0

    out = []
    for c in calls:
        if c.started_at:
            secs = int((now - c.started_at).total_seconds())
        else:
            secs = 0
        out.append(ActiveCallDetail(
            id=c.id,
            direction=str(c.direction.value) if hasattr(c.direction, "value") else str(c.direction),
            status=str(c.status.value) if hasattr(c.status, "value") else str(c.status),
            to_number=c.to_number,
            from_number=c.from_number,
            started_at=c.started_at,
            live_duration_seconds=secs,
            estimated_cost_cents=round(secs * cost_per_sec, 3),
            agent_name=c.agent_config.name if c.agent_config else None,
            contact_name=c.contact.name if c.contact else None,
            contact_id=c.contact_id,
        ))
    return out


@router.get("", response_model=PaginatedCalls)
async def list_calls(
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    status: Optional[str] = Query(default=None, description="Filter by status: dialing, active, completed, failed"),
    agent_id: Optional[str] = Query(default=None),
    session: AsyncSession = Depends(get_session),
):
    """Return a paginated list of calls (most recent first) with agent + contact names."""
    offset = (page - 1) * limit

    base_query = select(Call)
    if status:
        base_query = base_query.where(Call.status == status)
    if agent_id:
        base_query = base_query.where(Call.agent_config_id == agent_id)

    total_result = await session.execute(
        select(func.count()).select_from(base_query.subquery())
    )
    total = total_result.scalar_one()

    result = await session.execute(
        base_query
        .options(selectinload(Call.agent_config), selectinload(Call.contact))
        .order_by(Call.started_at.desc())
        .offset(offset)
        .limit(limit)
    )
    items_raw = result.scalars().all()

    items = []
    for c in items_raw:
        s = CallSummary.model_validate(c)
        s.agent_name = c.agent_config.name if c.agent_config else None
        s.agent_config_id = c.agent_config_id
        s.contact_name = c.contact.name if c.contact else None
        s.contact_id = c.contact_id
        items.append(s)

    return PaginatedCalls(total=total, page=page, limit=limit, items=items)


@router.get("/{call_id}", response_model=CallDetail)
async def get_call(
    call_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Return full call detail including all transcript turns."""
    result = await session.execute(
        select(Call)
        .where(Call.id == call_id)
        .options(selectinload(Call.turns))
    )
    call = result.scalar_one_or_none()
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    return call


@router.post("/{call_id}/hangup", response_model=CallSummary)
async def hangup_call(
    call_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Mark a call as completed (soft hangup — updates status only)."""
    call = await session.get(Call, call_id)
    if not call:
        raise HTTPException(status_code=404, detail="Call not found")
    if call.status in (CallStatus.completed, CallStatus.failed):
        return call

    call.status = CallStatus.completed
    now = datetime.now(UTC)
    if not call.ended_at:
        call.ended_at = now
    if call.started_at and call.ended_at:
        delta = call.ended_at - call.started_at
        call.duration_seconds = int(delta.total_seconds())
    await session.commit()
    await session.refresh(call)

    # Write post-call note to WhatsApp conversation if linked to a contact
    if call.contact_id:
        asyncio.create_task(_post_call_note(call_id))

    return call


async def _post_call_note(call_id: str):
    """After a call completes, write a note into the contact's WhatsApp conversation."""
    async with AsyncSessionLocal() as session:
        call = await session.get(Call, call_id, options=[selectinload(Call.turns)])
        if not call or not call.contact_id:
            return

        contact_q = await session.execute(
            select(Contact).where(Contact.id == call.contact_id)
        )
        contact = contact_q.scalar_one_or_none()
        if not contact:
            return

        # Find or create an open conversation for this contact
        conv_q = await session.execute(
            select(Conversation)
            .where(
                Conversation.contact_id == contact.id,
                Conversation.status != ConversationStatus.closed,
            )
            .order_by(Conversation.created_at.desc())
            .limit(1)
        )
        conv = conv_q.scalar_one_or_none()
        if not conv:
            conv = Conversation(
                contact_id=contact.id,
                user_id=contact.user_id,
                status=ConversationStatus.open,
            )
            session.add(conv)
            await session.flush()

        # Build note content from transcript summary (last 5 turns)
        recent_turns = sorted(call.turns or [], key=lambda t: t.started_at)[-5:]
        transcript_snippet = "\n".join(
            f"{t.role.upper()}: {t.text}" for t in recent_turns
        )
        duration_str = f"{call.duration_seconds or 0}s"
        note_text = (
            f"📞 Voice call completed ({duration_str})\n"
            f"Direction: {call.direction}\n"
            f"{'Number: ' + (call.from_number or call.to_number or 'unknown')}\n"
        )
        if transcript_snippet:
            note_text += f"\nTranscript (last {len(recent_turns)} turns):\n{transcript_snippet}"

        note = Message(
            conversation_id=conv.id,
            direction=MessageDirection.outbound,
            type=MessageType.note,
            content={"text": note_text, "call_id": call_id},
            status=MessageStatus.sent,
        )
        session.add(note)
        conv.last_message_at = datetime.now(UTC)
        await session.commit()
        logger.info(f"Post-call note written for call {call_id} → conversation {conv.id}")
