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
    # Voice broadcast fields
    broadcast_type: str = "whatsapp"  # "whatsapp" | "voice" | "sequence"
    agent_config_id: Optional[str] = None  # for voice broadcasts
    max_retries: int = 1  # voice: retry if no answer


class BroadcastResponse(BaseModel):
    id: str
    name: str
    status: str
    sent_count: int
    failed_count: int
    scheduled_at: Optional[datetime] = None
    created_at: datetime
    broadcast_type: str = "whatsapp"

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

    # Dispatch immediately if not scheduled
    if not payload.scheduled_at:
        if payload.broadcast_type == "voice":
            background_tasks.add_task(
                _send_voice_broadcast,
                broadcast.id,
                current_user.id,
                payload.agent_config_id,
                payload.max_retries,
            )
        elif payload.broadcast_type == "sequence":
            # WhatsApp first, then voice follow-up
            background_tasks.add_task(_send_broadcast, broadcast.id, current_user.id)
            background_tasks.add_task(
                _send_voice_broadcast,
                broadcast.id,
                current_user.id,
                payload.agent_config_id,
                payload.max_retries,
            )
        else:
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

async def _send_voice_broadcast(
    broadcast_id: str,
    user_id: str,
    agent_config_id: Optional[str],
    max_retries: int = 1,
):
    """Dispatch outbound voice calls to all recipients of a broadcast."""
    from voiceagent.telephony.livekit_sip import create_room, dial_outbound
    from voiceagent.agent.runner import run_agent
    from voiceagent.db.models import AgentConfig, Call, CallDirection, CallStatus
    import uuid

    async with AsyncSessionLocal() as session:
        bc_q = await session.execute(select(Broadcast).where(Broadcast.id == broadcast_id))
        bc = bc_q.scalar_one_or_none()
        if not bc:
            return

        bc.status = BroadcastStatus.sending
        await session.commit()

        # Load agent config
        agent_cfg = None
        if agent_config_id:
            ac = await session.get(AgentConfig, agent_config_id)
            if ac:
                agent_cfg = {"id": ac.id, "system_prompt": ac.system_prompt, "voice_id": ac.voice_id, "llm_model": ac.llm_model}

        # Get all pending recipients
        recips_q = await session.execute(
            select(BroadcastRecipient).where(BroadcastRecipient.broadcast_id == broadcast_id)
        )
        recipients = list(recips_q.scalars().all())

        for recipient in recipients:
            contact_q = await session.execute(select(Contact).where(Contact.id == recipient.contact_id))
            contact = contact_q.scalar_one_or_none()
            if not contact or not contact.phone:
                recipient.status = MessageStatus.failed
                recipient.error = "No phone number"
                bc.failed_count += 1
                continue

            for attempt in range(max(1, max_retries)):
                try:
                    room_name = f"bc-{uuid.uuid4().hex[:10]}"
                    call_id = str(uuid.uuid4())
                    from datetime import UTC
                    now = __import__("datetime").datetime.now(UTC)

                    await create_room(room_name)

                    call = Call(
                        id=call_id,
                        agent_config_id=agent_config_id,
                        contact_id=contact.id,
                        direction=CallDirection.outbound,
                        status=CallStatus.dialing,
                        to_number=contact.phone,
                        livekit_room_name=room_name,
                        started_at=now,
                    )
                    session.add(call)
                    await session.flush()

                    await dial_outbound(contact.phone, room_name)
                    asyncio.create_task(run_agent(call_id, room_name, agent_cfg))

                    recipient.status = MessageStatus.sent
                    bc.sent_count += 1
                    logger.info(f"Voice broadcast {broadcast_id}: called {contact.phone} (call={call_id})")
                    break
                except Exception as e:
                    logger.error(f"Voice broadcast {broadcast_id}: call to {contact.phone} failed (attempt {attempt+1}): {e}")
                    if attempt == max_retries - 1:
                        recipient.status = MessageStatus.failed
                        recipient.error = str(e)
                        bc.failed_count += 1

        bc.status = BroadcastStatus.completed
        await session.commit()
        logger.info(f"Voice broadcast {broadcast_id} done: sent={bc.sent_count} failed={bc.failed_count}")
