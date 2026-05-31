"""Conversations CRUD."""
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from voiceagent.api.auth import get_current_user
from voiceagent.db.models import (
    Contact, Conversation, ConversationStatus, Message, User,
)
from voiceagent.db.session import get_session

router = APIRouter(prefix="/conversations", tags=["conversations"])


class ContactSummary(BaseModel):
    id: str
    name: Optional[str] = None
    phone: str

    model_config = {"from_attributes": True}


class LastMessageSummary(BaseModel):
    id: str
    direction: str
    type: str
    content: dict
    created_at: datetime

    model_config = {"from_attributes": True}


class ConversationResponse(BaseModel):
    id: str
    contact_id: str
    status: str
    assigned_to: Optional[str] = None
    last_message_at: Optional[datetime] = None
    created_at: datetime
    contact: Optional[ContactSummary] = None
    last_message: Optional[LastMessageSummary] = None

    model_config = {"from_attributes": True}


class ConversationUpdate(BaseModel):
    status: Optional[ConversationStatus] = None
    assigned_to: Optional[str] = None


async def _get_conv(conv_id: str, user_id: str, session: AsyncSession) -> Conversation:
    result = await session.execute(
        select(Conversation)
        .where(Conversation.id == conv_id, Conversation.user_id == user_id)
        .options(selectinload(Conversation.contact))
    )
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Conversation not found")
    return c


async def _with_last_message(conv: Conversation, session: AsyncSession) -> ConversationResponse:
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conv.id)
        .order_by(Message.created_at.desc())
        .limit(1)
    )
    last = result.scalar_one_or_none()
    resp = ConversationResponse.model_validate(conv)
    if last:
        resp.last_message = LastMessageSummary(
            id=last.id,
            direction=str(last.direction.value) if hasattr(last.direction, "value") else str(last.direction),
            type=str(last.type.value) if hasattr(last.type, "value") else str(last.type),
            content=last.content or {},
            created_at=last.created_at,
        )
    return resp


@router.get("", response_model=list[ConversationResponse])
async def list_conversations(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Conversation)
        .where(Conversation.user_id == current_user.id)
        .options(selectinload(Conversation.contact))
    )
    if status:
        stmt = stmt.where(Conversation.status == status)
    stmt = stmt.order_by(Conversation.last_message_at.desc().nullslast()).offset(skip).limit(limit)
    result = await session.execute(stmt)
    convs = list(result.scalars().all())
    return [await _with_last_message(c, session) for c in convs]


@router.get("/contact/{contact_id}", response_model=ConversationResponse)
async def get_or_create_for_contact(
    contact_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    contact_q = await session.execute(
        select(Contact).where(Contact.id == contact_id, Contact.user_id == current_user.id)
    )
    if not contact_q.scalar_one_or_none():
        raise HTTPException(404, "Contact not found")

    result = await session.execute(
        select(Conversation)
        .where(Conversation.user_id == current_user.id, Conversation.contact_id == contact_id,
               Conversation.status != ConversationStatus.closed)
        .options(selectinload(Conversation.contact))
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        conv = Conversation(user_id=current_user.id, contact_id=contact_id)
        session.add(conv)
        await session.commit()
        await session.refresh(conv, attribute_names=["contact"])
    return await _with_last_message(conv, session)


@router.get("/{conversation_id}", response_model=ConversationResponse)
async def get_conversation(
    conversation_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    conv = await _get_conv(conversation_id, current_user.id, session)
    return await _with_last_message(conv, session)


@router.patch("/{conversation_id}", response_model=ConversationResponse)
async def update_conversation(
    conversation_id: str,
    payload: ConversationUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    conv = await _get_conv(conversation_id, current_user.id, session)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(conv, k, v)
    await session.commit()
    await session.refresh(conv, attribute_names=["contact"])
    return await _with_last_message(conv, session)
