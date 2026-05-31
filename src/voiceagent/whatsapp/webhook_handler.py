"""Parse inbound WhatsApp webhook events and persist to DB.

Called by POST /webhooks/whatsapp after HMAC verification.
"""
import asyncio
from datetime import datetime, UTC
from typing import Any

from loguru import logger
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voiceagent.db.models import (
    Contact, Conversation, ConversationStatus, Message,
    MessageDirection, MessageType, MessageStatus,
    WhatsAppConfig,
)
from voiceagent.whatsapp.phone_utils import normalize

# Per-conversation SSE queues: conversation_id -> list of asyncio.Queue
_sse_queues: dict[str, list[asyncio.Queue]] = {}


def register_sse_queue(conversation_id: str, q: asyncio.Queue):
    _sse_queues.setdefault(conversation_id, []).append(q)


def unregister_sse_queue(conversation_id: str, q: asyncio.Queue):
    queues = _sse_queues.get(conversation_id, [])
    if q in queues:
        queues.remove(q)


async def _push_sse(conversation_id: str, event: dict):
    for q in list(_sse_queues.get(conversation_id, [])):
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def handle_webhook(payload: dict, session: AsyncSession):
    """Entry point — parse the Meta webhook payload and persist."""
    try:
        entry = payload.get("entry", [])
        for e in entry:
            for change in e.get("changes", []):
                value = change.get("value", {})
                await _process_value(value, session)
    except Exception as exc:
        logger.error(f"Webhook handler error: {exc}")


async def _process_value(value: dict, session: AsyncSession):
    metadata = value.get("metadata", {})
    phone_number_id = metadata.get("phone_number_id", "")

    # Find the WhatsApp config for this phone number
    result = await session.execute(
        select(WhatsAppConfig).where(
            WhatsAppConfig.phone_number_id == phone_number_id
        )
    )
    wa_config = result.scalar_one_or_none()
    if not wa_config:
        logger.warning(f"No config found for phone_number_id={phone_number_id}")
        return

    owner_user_id = wa_config.user_id

    # Handle inbound messages
    for msg in value.get("messages", []):
        await _handle_message(msg, owner_user_id, session)

    # Handle status updates (delivered, read, failed)
    for status in value.get("statuses", []):
        await _handle_status(status, session)


async def _handle_message(msg: dict, owner_user_id: str, session: AsyncSession):
    wa_id = msg.get("from")  # sender phone
    wa_message_id = msg.get("id")
    timestamp = msg.get("timestamp")
    msg_type = msg.get("type", "text")

    if not wa_id:
        return

    phone = normalize(wa_id)
    contact = await _upsert_contact(phone, owner_user_id, session)
    conversation = await _upsert_conversation(contact, owner_user_id, session)

    # Build content dict based on message type
    content = _extract_content(msg, msg_type)

    # Deduplicate by wa_message_id
    if wa_message_id:
        existing = await session.execute(
            select(Message).where(Message.wa_message_id == wa_message_id)
        )
        if existing.scalar_one_or_none():
            return

    db_msg = Message(
        conversation_id=conversation.id,
        direction=MessageDirection.inbound,
        type=_map_type(msg_type),
        content=content,
        wa_message_id=wa_message_id,
        status=MessageStatus.delivered,
    )
    session.add(db_msg)

    # Update conversation last_message_at
    conversation.last_message_at = datetime.now(UTC)
    conversation.status = ConversationStatus.open

    await session.commit()
    await session.refresh(db_msg)

    await _push_sse(conversation.id, {
        "event": "new_message",
        "message_id": db_msg.id,
        "direction": "inbound",
        "type": db_msg.type,
        "content": content,
        "created_at": db_msg.created_at.isoformat(),
    })
    logger.info(f"Inbound message {db_msg.id} saved for conversation {conversation.id}")


async def _handle_status(status: dict, session: AsyncSession):
    wa_message_id = status.get("id")
    new_status = status.get("status")
    if not wa_message_id:
        return

    result = await session.execute(
        select(Message).where(Message.wa_message_id == wa_message_id)
    )
    msg = result.scalar_one_or_none()
    if not msg:
        return

    status_map = {
        "sent": MessageStatus.sent,
        "delivered": MessageStatus.delivered,
        "read": MessageStatus.read,
        "failed": MessageStatus.failed,
    }
    if new_status in status_map:
        msg.status = status_map[new_status]
        await session.commit()
        await _push_sse(msg.conversation_id, {
            "event": "status_update",
            "message_id": msg.id,
            "status": new_status,
        })


async def _upsert_contact(phone: str, user_id: str, session: AsyncSession) -> Contact:
    result = await session.execute(
        select(Contact).where(Contact.user_id == user_id, Contact.phone == phone)
    )
    contact = result.scalar_one_or_none()
    if not contact:
        contact = Contact(user_id=user_id, phone=phone, name=phone)
        session.add(contact)
        await session.flush()  # get ID without committing
    return contact


async def _upsert_conversation(
    contact: Contact, user_id: str, session: AsyncSession
) -> Conversation:
    # Find most recent open/pending conversation
    result = await session.execute(
        select(Conversation)
        .where(
            Conversation.contact_id == contact.id,
            Conversation.user_id == user_id,
            Conversation.status != ConversationStatus.closed,
        )
        .order_by(Conversation.created_at.desc())
        .limit(1)
    )
    conv = result.scalar_one_or_none()
    if not conv:
        conv = Conversation(contact_id=contact.id, user_id=user_id)
        session.add(conv)
        await session.flush()
    return conv


def _map_type(wa_type: str) -> MessageType:
    return {
        "text": MessageType.text,
        "image": MessageType.image,
        "video": MessageType.video,
        "audio": MessageType.audio,
        "document": MessageType.document,
        "location": MessageType.location,
        "template": MessageType.template,
        "interactive": MessageType.interactive,
        "button": MessageType.interactive,
        "list": MessageType.interactive,
    }.get(wa_type, MessageType.text)


def _extract_content(msg: dict, msg_type: str) -> dict:
    if msg_type == "text":
        return {"text": msg.get("text", {}).get("body", "")}
    if msg_type in ("image", "video", "audio", "document"):
        media = msg.get(msg_type, {})
        return {
            "media_id": media.get("id"),
            "mime_type": media.get("mime_type"),
            "caption": media.get("caption"),
            "filename": media.get("filename"),
        }
    if msg_type == "location":
        loc = msg.get("location", {})
        return {"latitude": loc.get("latitude"), "longitude": loc.get("longitude"), "name": loc.get("name")}
    if msg_type == "interactive":
        inter = msg.get("interactive", {})
        reply = inter.get("button_reply") or inter.get("list_reply") or {}
        return {"type": inter.get("type"), "id": reply.get("id"), "title": reply.get("title")}
    if msg_type == "button":
        btn = msg.get("button", {})
        return {"text": btn.get("text"), "payload": btn.get("payload")}
    return {"raw": msg}
