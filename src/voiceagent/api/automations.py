"""Automations CRUD."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voiceagent.api.auth import get_current_user
from voiceagent.db.models import Automation, User
from voiceagent.db.session import get_session

router = APIRouter(prefix="/automations", tags=["automations"])


class AutomationCreate(BaseModel):
    name: str
    trigger_type: str = "message_received"
    trigger_config: Optional[dict] = None
    steps: Optional[list] = None


class AutomationResponse(BaseModel):
    id: str
    name: str
    trigger_type: str
    is_active: bool
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


@router.get("", response_model=list[AutomationResponse])
async def list_automations(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Automation)
        .where(Automation.user_id == current_user.id)
        .order_by(Automation.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("", response_model=AutomationResponse, status_code=201)
async def create_automation(
    payload: AutomationCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    auto = Automation(
        user_id=current_user.id,
        name=payload.name,
        trigger_type=payload.trigger_type,
        trigger_config=payload.trigger_config,
        steps=payload.steps,
    )
    session.add(auto)
    await session.commit()
    await session.refresh(auto)
    return auto


@router.get("/{automation_id}", response_model=AutomationResponse)
async def get_automation(
    automation_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Automation).where(
            Automation.id == automation_id, Automation.user_id == current_user.id
        )
    )
    auto = result.scalar_one_or_none()
    if not auto:
        raise HTTPException(404, "Automation not found")
    return auto


@router.patch("/{automation_id}", response_model=AutomationResponse)
async def update_automation(
    automation_id: str,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Automation).where(
            Automation.id == automation_id, Automation.user_id == current_user.id
        )
    )
    auto = result.scalar_one_or_none()
    if not auto:
        raise HTTPException(404, "Automation not found")
    for k, v in payload.items():
        if hasattr(auto, k):
            setattr(auto, k, v)
    await session.commit()
    await session.refresh(auto)
    return auto


@router.delete("/{automation_id}", status_code=204)
async def delete_automation(
    automation_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Automation).where(
            Automation.id == automation_id, Automation.user_id == current_user.id
        )
    )
    auto = result.scalar_one_or_none()
    if not auto:
        raise HTTPException(404, "Automation not found")
    await session.delete(auto)
    await session.commit()


@router.post("/{automation_id}/duplicate", response_model=AutomationResponse, status_code=201)
async def duplicate_automation(
    automation_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Automation).where(
            Automation.id == automation_id, Automation.user_id == current_user.id
        )
    )
    auto = result.scalar_one_or_none()
    if not auto:
        raise HTTPException(404, "Automation not found")
    copy = Automation(
        user_id=current_user.id,
        name=f"{auto.name} (copy)",
        trigger_type=auto.trigger_type,
        trigger_config=auto.trigger_config,
        steps=auto.steps,
    )
    session.add(copy)
    await session.commit()
    await session.refresh(copy)
    return copy
