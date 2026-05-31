"""Pipelines, stages, and deals CRUD."""
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from voiceagent.api.auth import get_current_user
from voiceagent.db.models import (
    Contact, Deal, DealStatus, Pipeline, PipelineStage, User,
)
from voiceagent.db.session import get_session

router = APIRouter(prefix="/pipelines", tags=["pipelines"])
deals_router = APIRouter(prefix="/deals", tags=["deals"])


class StageResponse(BaseModel):
    id: str
    pipeline_id: str
    name: str
    position: int
    color: Optional[str] = None

    model_config = {"from_attributes": True}


class StageCreate(BaseModel):
    name: str
    position: int = 0
    color: Optional[str] = None


class StageUpdate(BaseModel):
    name: Optional[str] = None
    position: Optional[int] = None
    color: Optional[str] = None


class PipelineResponse(BaseModel):
    id: str
    name: str
    created_at: datetime
    stages: list[StageResponse] = []

    model_config = {"from_attributes": True}


class PipelineCreate(BaseModel):
    name: str


class DealResponse(BaseModel):
    id: str
    pipeline_id: str
    stage_id: str
    contact_id: Optional[str] = None
    title: str
    value: Optional[float] = None
    status: str
    close_date: Optional[datetime] = None
    contact_name: Optional[str] = None
    contact_phone: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class DealCreate(BaseModel):
    pipeline_id: str
    stage_id: str
    contact_id: Optional[str] = None
    title: str
    value: Optional[float] = None
    close_date: Optional[datetime] = None


class DealUpdate(BaseModel):
    stage_id: Optional[str] = None
    title: Optional[str] = None
    value: Optional[float] = None
    status: Optional[DealStatus] = None
    close_date: Optional[datetime] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_pipeline(pid: str, user_id: str, session: AsyncSession) -> Pipeline:
    result = await session.execute(
        select(Pipeline)
        .where(Pipeline.id == pid, Pipeline.user_id == user_id)
        .options(selectinload(Pipeline.stages))
    )
    p = result.scalar_one_or_none()
    if not p:
        raise HTTPException(404, "Pipeline not found")
    return p


async def _get_stage(pid: str, sid: str, user_id: str, session: AsyncSession) -> PipelineStage:
    await _get_pipeline(pid, user_id, session)
    result = await session.execute(
        select(PipelineStage).where(PipelineStage.id == sid, PipelineStage.pipeline_id == pid)
    )
    s = result.scalar_one_or_none()
    if not s:
        raise HTTPException(404, "Stage not found")
    return s


async def _get_deal(deal_id: str, user_id: str, session: AsyncSession) -> Deal:
    # Verify ownership via pipeline
    result = await session.execute(
        select(Deal).where(Deal.id == deal_id)
    )
    d = result.scalar_one_or_none()
    if not d:
        raise HTTPException(404, "Deal not found")
    # Verify the pipeline belongs to user
    await _get_pipeline(d.pipeline_id, user_id, session)
    return d


def _deal_resp(deal: Deal, contact: Optional[Contact]) -> DealResponse:
    return DealResponse(
        id=deal.id,
        pipeline_id=deal.pipeline_id,
        stage_id=deal.stage_id,
        contact_id=deal.contact_id,
        title=deal.title,
        value=deal.value,
        status=str(deal.status.value) if hasattr(deal.status, "value") else str(deal.status),
        close_date=deal.close_date,
        contact_name=contact.name if contact else None,
        contact_phone=contact.phone if contact else None,
        created_at=deal.created_at,
    )


# ---------------------------------------------------------------------------
# Pipeline endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[PipelineResponse])
async def list_pipelines(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Pipeline)
        .where(Pipeline.user_id == current_user.id)
        .options(selectinload(Pipeline.stages))
        .order_by(Pipeline.created_at.asc())
    )
    return list(result.scalars().all())


@router.post("", response_model=PipelineResponse, status_code=201)
async def create_pipeline(
    payload: PipelineCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    p = Pipeline(user_id=current_user.id, name=payload.name)
    session.add(p)
    await session.commit()
    await session.refresh(p, attribute_names=["stages"])
    return p


@router.delete("/{pipeline_id}", status_code=204)
async def delete_pipeline(
    pipeline_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    p = await _get_pipeline(pipeline_id, current_user.id, session)
    await session.delete(p)
    await session.commit()


@router.post("/{pipeline_id}/stages", response_model=StageResponse, status_code=201)
async def create_stage(
    pipeline_id: str,
    payload: StageCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _get_pipeline(pipeline_id, current_user.id, session)
    stage = PipelineStage(
        pipeline_id=pipeline_id, name=payload.name,
        position=payload.position, color=payload.color,
    )
    session.add(stage)
    await session.commit()
    await session.refresh(stage)
    return stage


@router.patch("/{pipeline_id}/stages/{stage_id}", response_model=StageResponse)
async def update_stage(
    pipeline_id: str,
    stage_id: str,
    payload: StageUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stage = await _get_stage(pipeline_id, stage_id, current_user.id, session)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(stage, k, v)
    await session.commit()
    await session.refresh(stage)
    return stage


@router.delete("/{pipeline_id}/stages/{stage_id}", status_code=204)
async def delete_stage(
    pipeline_id: str,
    stage_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stage = await _get_stage(pipeline_id, stage_id, current_user.id, session)
    await session.delete(stage)
    await session.commit()


# ---------------------------------------------------------------------------
# Deal endpoints
# ---------------------------------------------------------------------------

@deals_router.get("", response_model=list[DealResponse])
async def list_deals(
    pipeline_id: Optional[str] = Query(None),
    stage_id: Optional[str] = Query(None),
    status_filter: Optional[str] = Query(None, alias="status"),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # Join through pipeline to verify ownership
    stmt = (
        select(Deal)
        .join(Pipeline, Deal.pipeline_id == Pipeline.id)
        .where(Pipeline.user_id == current_user.id)
    )
    if pipeline_id:
        stmt = stmt.where(Deal.pipeline_id == pipeline_id)
    if stage_id:
        stmt = stmt.where(Deal.stage_id == stage_id)
    if status_filter:
        stmt = stmt.where(Deal.status == status_filter)
    stmt = stmt.order_by(Deal.created_at.desc())
    result = await session.execute(stmt)
    deals = list(result.scalars().all())

    out = []
    for d in deals:
        contact = None
        if d.contact_id:
            cq = await session.execute(select(Contact).where(Contact.id == d.contact_id))
            contact = cq.scalar_one_or_none()
        out.append(_deal_resp(d, contact))
    return out


@deals_router.post("", response_model=DealResponse, status_code=201)
async def create_deal(
    payload: DealCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    await _get_stage(payload.pipeline_id, payload.stage_id, current_user.id, session)
    contact = None
    if payload.contact_id:
        cq = await session.execute(
            select(Contact).where(Contact.id == payload.contact_id, Contact.user_id == current_user.id)
        )
        contact = cq.scalar_one_or_none()
        if not contact:
            raise HTTPException(404, "Contact not found")

    deal = Deal(
        pipeline_id=payload.pipeline_id,
        stage_id=payload.stage_id,
        contact_id=payload.contact_id,
        title=payload.title,
        value=payload.value,
        close_date=payload.close_date,
        status=DealStatus.open,
    )
    session.add(deal)
    await session.commit()
    await session.refresh(deal)
    return _deal_resp(deal, contact)


@deals_router.patch("/{deal_id}", response_model=DealResponse)
async def update_deal(
    deal_id: str,
    payload: DealUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    deal = await _get_deal(deal_id, current_user.id, session)
    data = payload.model_dump(exclude_unset=True)
    if "stage_id" in data and data["stage_id"]:
        await _get_stage(deal.pipeline_id, data["stage_id"], current_user.id, session)
    for k, v in data.items():
        setattr(deal, k, v)
    await session.commit()
    await session.refresh(deal)
    contact = None
    if deal.contact_id:
        cq = await session.execute(select(Contact).where(Contact.id == deal.contact_id))
        contact = cq.scalar_one_or_none()
    return _deal_resp(deal, contact)


@deals_router.delete("/{deal_id}", status_code=204)
async def delete_deal(
    deal_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    deal = await _get_deal(deal_id, current_user.id, session)
    await session.delete(deal)
    await session.commit()
