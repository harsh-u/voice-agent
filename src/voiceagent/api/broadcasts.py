"""Broadcasts — bulk WhatsApp template sends."""
import asyncio
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voiceagent.api.auth import get_current_user
from voiceagent.db.models import (
    Broadcast, BroadcastRecipient, BroadcastStatus,
    Contact, MessageStatus, MessageTemplate, User, WhatsAppConfig,
)
from voiceagent.db.session import get_session, AsyncSessionLocal
from voiceagent.whatsapp.encryption import decrypt
from voiceagent.whatsapp.meta_api import MetaAPI

router = APIRouter(prefix="/broadcasts", tags=["broadcasts"])


class BroadcastCreate(BaseModel):
    name: str
    template_id: Optional[str] = None
    contact_ids: list[str]
    scheduled_at: Optional[datetime] = None


class BroadcastResponse(BaseModel):
    id: str
    name: str
    status: str
    sent_count: int
    failed_count: int
    scheduled_at: Optional[datetime] = None
    created_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[BroadcastResponse])
async def list_broadcasts(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Broadcast)
        .where(Broadcast.user_id == current_user.id)
        .order_by(Broadcast.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=BroadcastResponse, status_code=201)
async def create_broadcast(
    payload: BroadcastCreate,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    broadcast = Broadcast(
        user_id=current_user.id,
        template_id=payload.template_id,
        name=payload.name,
        status=BroadcastStatus.draft,
        scheduled_at=payload.scheduled_at,
    )
    session.add(broadcast)
    await session.flush()

    for cid in payload.contact_ids:
        session.add(BroadcastRecipient(
            broadcast_id=broadcast.id,
            contact_id=cid,
            status=MessageStatus.pending,
        ))

    await session.commit()
    await session.refresh(broadcast)

    # If no schedule, send immediately in background
    if not payload.scheduled_at:
        background_tasks.add_task(_send_broadcast, broadcast.id, current_user.id)

    return broadcast


@router.post("/{broadcast_id}/send", response_model=BroadcastResponse)
async def send_broadcast(
    broadcast_id: str,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Broadcast).where(
            Broadcast.id == broadcast_id, Broadcast.user_id == current_user.id
        )
    )
    bc = result.scalar_one_or_none()
    if not bc:
        raise HTTPException(404, "Broadcast not found")
    background_tasks.add_task(_send_broadcast, broadcast_id, current_user.id)
    return bc


@router.delete("/{broadcast_id}", status_code=204)
async def delete_broadcast(
    broadcast_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Broadcast).where(
            Broadcast.id == broadcast_id, Broadcast.user_id == current_user.id
        )
    )
    bc = result.scalar_one_or_none()
    if not bc:
        raise HTTPException(404, "Broadcast not found")
    await session.delete(bc)
    await session.commit()


async def _send_broadcast(broadcast_id: str, user_id: str):
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(Broadcast).where(Broadcast.id == broadcast_id)
        )
        bc = result.scalar_one_or_none()
        if not bc:
            return

        bc.status = BroadcastStatus.sending
        await session.commit()

        cfg_q = await session.execute(
            select(WhatsAppConfig).where(WhatsAppConfig.user_id == user_id)
        )
        cfg = cfg_q.scalar_one_or_none()
        if not cfg or not cfg.access_token_enc:
            bc.status = BroadcastStatus.failed
            await session.commit()
            return

        api = MetaAPI(
            phone_number_id=cfg.phone_number_id,
            access_token=decrypt(cfg.access_token_enc),
        )

        template_name = None
        if bc.template_id:
            tq = await session.execute(
                select(MessageTemplate).where(MessageTemplate.id == bc.template_id)
            )
            tmpl = tq.scalar_one_or_none()
            if tmpl:
                template_name = tmpl.name

        recipients_q = await session.execute(
            select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id)
        )
        recipients = list(recipients_q.scalars().all())

        for recipient in recipients:
            contact_q = await session.execute(
                select(Contact).where(Contact.id == recipient.contact_id)
            )
            contact = contact_q.scalar_one_or_none()
            if not contact:
                recipient.status = MessageStatus.failed
                recipient.error = "Contact not found"
                bc.failed_count += 1
                continue
            try:
                if template_name:
                    await api.send_template(contact.phone, template_name)
                else:
                    await api.send_text(contact.phone, bc.name)
                recipient.status = MessageStatus.sent
                bc.sent_count += 1
            except Exception as e:
                recipient.status = MessageStatus.failed
                recipient.error = str(e)
                bc.failed_count += 1
                logger.error(f"Broadcast {broadcast_id} failed for contact {contact.id}: {e}")

        bc.status = BroadcastStatus.completed
        await session.commit()
        logger.info(f"Broadcast {broadcast_id} complete: sent={bc.sent_count} failed={bc.failed_count}")
