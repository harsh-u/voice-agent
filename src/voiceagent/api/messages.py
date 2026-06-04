"""Messages send/list + SSE realtime stream."""
import asyncio
import json
from datetime import datetime, UTC
from typing import Any, AsyncIterator, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sse_starlette.sse import EventSourceResponse

from voiceagent.api.auth import get_current_user
from voiceagent.db.models import (
    Contact, Conversation, Message, MessageDirection,
    MessageStatus, MessageType, User, WhatsAppConfig,
)
from voiceagent.db.session import get_session
from voiceagent.whatsapp.encryption import decrypt
from voiceagent.whatsapp.meta_api import MetaAPI
from voiceagent.whatsapp.webhook_handler import register_sse_queue, unregister_sse_queue

router = APIRouter(tags=["messages"])


class MessageResponse(BaseModel):
    id: str
    conversation_id: str
    direction: str
    type: str
    content: dict
    status: str
    wa_message_id: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class MessageCreate(BaseModel):
    type: MessageType = MessageType.text
    content: dict  # e.g. {"text": "Hello"} or {"url": "...", "caption": "..."}


async def _get_conv(conv_id: str, user_id: str, session: AsyncSession) -> Conversation:
    result = await session.execute(
        select(Conversation).where(Conversation.id == conv_id, Conversation.user_id == user_id)
    )
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Conversation not found")
    return c


@router.get("/messages/{conversation_id}", response_model=list[MessageResponse])
async def list_messages(
    conversation_id: str,
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=500),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _get_conv(conversation_id, current_user.id, session)
    result = await session.execute(
        select(Message)
        .where(Message.conversation_id == conversation_id)
        .order_by(Message.created_at.asc())
        .offset(skip)
        .limit(limit)
    )
    return list(result.scalars().all())


@router.post("/messages/{conversation_id}", response_model=MessageResponse, status_code=201)
async def send_message(
    conversation_id: str,
    payload: MessageCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    conv = await _get_conv(conversation_id, current_user.id, session)

    # Resolve contact phone
    contact_q = await session.execute(select(Contact).where(Contact.id == conv.contact_id))
    contact = contact_q.scalar_one()

    msg = Message(
        conversation_id=conv.id,
        direction=MessageDirection.outbound,
        type=payload.type,
        content=payload.content,
        status=MessageStatus.pending,
    )
    session.add(msg)
    await session.commit()
    await session.refresh(msg)

    # Try to send via WhatsApp
    cfg_q = await session.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.user_id == current_user.id)
    )
    cfg = cfg_q.scalar_one_or_none()

    if cfg and cfg.access_token_enc:
        try:
            api = MetaAPI(
                phone_number_id=cfg.phone_number_id,
                access_token=decrypt(cfg.access_token_enc),
            )
            if payload.type == MessageType.text:
                wa_resp = await api.send_text(contact.phone, payload.content.get("text", ""))
            elif payload.type == MessageType.template:
                wa_resp = await api.send_template(
                    contact.phone,
                    payload.content.get("name", ""),
                    payload.content.get("language", "en_US"),
                    payload.content.get("components"),
                )
            else:
                wa_resp = await api.send_media(
                    contact.phone,
                    media_type=payload.type.value,
                    media_url=payload.content.get("url"),
                    media_id=payload.content.get("media_id"),
                    caption=payload.content.get("caption"),
                )

            wa_msgs = wa_resp.get("messages", []) if isinstance(wa_resp, dict) else []
            wa_id = wa_msgs[0].get("id") if wa_msgs else None

            # Use raw SQL UPDATE to avoid stale-object issues with the session
            from sqlalchemy import update as sa_update
            now = datetime.now(UTC)
            await session.execute(
                sa_update(Message)
                .where(Message.id == msg.id)
                .values(wa_message_id=wa_id, status=MessageStatus.sent)
            )
            await session.execute(
                sa_update(Conversation)
                .where(Conversation.id == conv.id)
                .values(last_message_at=now)
            )
            await session.commit()
            logger.info(f"Sent message {msg.id} via WhatsApp (wa_id={wa_id})")
            # Return a clean response dict — don't try to re-fetch from session to avoid stale errors
            return {
                "id": msg.id,
                "conversation_id": conv.id,
                "direction": "outbound",
                "type": str(payload.type.value) if hasattr(payload.type, "value") else str(payload.type),
                "content": payload.content,
                "status": "sent",
                "wa_message_id": wa_id,
                "created_at": msg.created_at.isoformat() if msg.created_at else None,
            }
        except Exception as e:
            logger.error(f"WhatsApp send failed for message {msg.id}: {e}")
            try:
                from sqlalchemy import update as sa_update2
                await session.execute(
                    sa_update2(Message).where(Message.id == msg.id).values(status=MessageStatus.failed)
                )
                await session.commit()
            except Exception:
                pass
            raise HTTPException(502, f"WhatsApp send failed: {e}")
    else:
        logger.warning(f"No WhatsApp config for user {current_user.id}; message {msg.id} pending")

    return msg


@router.get("/conversations/{conversation_id}/sse")
async def conversation_sse(
    conversation_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _get_conv(conversation_id, current_user.id, session)
    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    register_sse_queue(conversation_id, q)
    logger.info(f"SSE subscribed: conversation={conversation_id} user={current_user.id}")

    async def gen() -> AsyncIterator[dict]:
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    event = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield {"event": event.get("event", "message"), "data": json.dumps(event)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            unregister_sse_queue(conversation_id, q)
            logger.info(f"SSE disconnected: conversation={conversation_id}")

    return EventSourceResponse(gen())
