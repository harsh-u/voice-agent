"""Flows CRUD — conversation flow builder."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voiceagent.api.auth import get_current_user
from voiceagent.db.models import Flow, User
from voiceagent.db.session import get_session

router = APIRouter(prefix="/flows", tags=["flows"])

FLOW_TEMPLATES = [
    {"id": "welcome", "name": "Welcome Message", "description": "Auto-reply to first-time contacts with a greeting."},
    {"id": "faq", "name": "FAQ Bot", "description": "Answer common questions automatically."},
    {"id": "lead-qual", "name": "Lead Qualifier", "description": "Ask qualification questions to filter inbound leads."},
    {"id": "support", "name": "Support Triage", "description": "Route customers to the right support agent."},
]


class FlowCreate(BaseModel):
    name: str
    template_id: Optional[str] = None


class FlowResponse(BaseModel):
    id: str
    name: str
    is_active: bool
    created_at: datetime
    updated_at: datetime
    nodes: Optional[list] = None
    edges: Optional[list] = None

    model_config = {"from_attributes": True}


@router.get("", response_model=list[FlowResponse])
async def list_flows(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Flow).where(Flow.user_id == current_user.id).order_by(Flow.created_at.desc())
    )
    return list(result.scalars().all())


@router.get("/templates")
async def list_templates():
    return FLOW_TEMPLATES


@router.post("", response_model=FlowResponse, status_code=201)
async def create_flow(
    payload: FlowCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    flow = Flow(user_id=current_user.id, name=payload.name)
    session.add(flow)
    await session.commit()
    await session.refresh(flow)
    return flow


@router.get("/{flow_id}", response_model=FlowResponse)
async def get_flow(
    flow_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Flow).where(Flow.id == flow_id, Flow.user_id == current_user.id)
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(404, "Flow not found")
    return flow


@router.patch("/{flow_id}", response_model=FlowResponse)
async def update_flow(
    flow_id: str,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Flow).where(Flow.id == flow_id, Flow.user_id == current_user.id)
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(404, "Flow not found")
    for k, v in payload.items():
        if hasattr(flow, k):
            setattr(flow, k, v)
    await session.commit()
    await session.refresh(flow)
    return flow


@router.delete("/{flow_id}", status_code=204)
async def delete_flow(
    flow_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Flow).where(Flow.id == flow_id, Flow.user_id == current_user.id)
    )
    flow = result.scalar_one_or_none()
    if not flow:
        raise HTTPException(404, "Flow not found")
    await session.delete(flow)
    await session.commit()


@router.get("/{flow_id}/runs")
async def get_flow_runs(
    flow_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return []
