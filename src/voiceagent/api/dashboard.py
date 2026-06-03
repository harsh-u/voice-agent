"""Dashboard metrics — aggregated stats for voice + WhatsApp."""
from datetime import datetime, timedelta, UTC

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from voiceagent.api.auth import get_current_user
from voiceagent.db.models import (
    Call, CallStatus, Conversation, ConversationStatus,
    Deal, DealStatus, Message, MessageDirection, Pipeline, User,
)
from voiceagent.db.session import get_session

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardMetrics(BaseModel):
    conversations_today: int
    open_conversations: int
    messages_today: int
    deals_open: int
    deals_won: int
    pipeline_value: float
    calls_today: int
    active_calls: int
    avg_call_duration_seconds: float
    # Voice cost & performance
    voice_spend_today_cents: float
    voice_answer_rate: float    # completed / (completed + failed) outbound calls today
    outbound_calls_today: int


@router.get("/metrics", response_model=DashboardMetrics)
async def get_metrics(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    now = datetime.now(UTC)
    today = now.replace(hour=0, minute=0, second=0, microsecond=0)

    # Conversations started today
    conv_today = await session.execute(
        select(func.count(Conversation.id)).where(
            Conversation.user_id == current_user.id,
            Conversation.created_at >= today,
        )
    )

    # Open conversations
    open_convs = await session.execute(
        select(func.count(Conversation.id)).where(
            Conversation.user_id == current_user.id,
            Conversation.status == ConversationStatus.open,
        )
    )

    # Messages sent/received today (via conversations owned by user)
    msgs_today_sq = (
        select(Conversation.id)
        .where(Conversation.user_id == current_user.id)
        .scalar_subquery()
    )
    msgs_today = await session.execute(
        select(func.count(Message.id)).where(
            Message.conversation_id.in_(msgs_today_sq),
            Message.created_at >= today,
        )
    )

    # Deals
    deals_open = await session.execute(
        select(func.count(Deal.id))
        .join(Pipeline, Deal.pipeline_id == Pipeline.id)
        .where(Pipeline.user_id == current_user.id, Deal.status == DealStatus.open)
    )
    deals_won = await session.execute(
        select(func.count(Deal.id))
        .join(Pipeline, Deal.pipeline_id == Pipeline.id)
        .where(Pipeline.user_id == current_user.id, Deal.status == DealStatus.won)
    )
    pipeline_value_q = await session.execute(
        select(func.coalesce(func.sum(Deal.value), 0.0))
        .join(Pipeline, Deal.pipeline_id == Pipeline.id)
        .where(Pipeline.user_id == current_user.id, Deal.status == DealStatus.open)
    )

    # Voice calls today
    calls_today = await session.execute(
        select(func.count(Call.id)).where(Call.started_at >= today)
    )
    active_calls = await session.execute(
        select(func.count(Call.id)).where(Call.status == CallStatus.active)
    )
    avg_duration_q = await session.execute(
        select(func.coalesce(func.avg(Call.duration_seconds), 0.0)).where(
            Call.status == CallStatus.completed,
            Call.started_at >= today,
        )
    )
    # Voice spend today
    spend_today_q = await session.execute(
        select(func.coalesce(func.sum(Call.cost_cents), 0.0)).where(
            Call.started_at >= today
        )
    )
    # Answer rate: completed outbound / (completed + failed) outbound today
    completed_out_q = await session.execute(
        select(func.count(Call.id)).where(
            Call.started_at >= today,
            Call.status == CallStatus.completed,
        )
    )
    failed_out_q = await session.execute(
        select(func.count(Call.id)).where(
            Call.started_at >= today,
            Call.status.in_([CallStatus.failed]),
        )
    )
    outbound_today_q = await session.execute(
        select(func.count(Call.id)).where(
            Call.started_at >= today,
            Call.direction == "outbound",
        )
    )
    completed_out = completed_out_q.scalar() or 0
    failed_out = failed_out_q.scalar() or 0
    denom = completed_out + failed_out
    answer_rate = round(completed_out / denom * 100, 1) if denom > 0 else 0.0

    return DashboardMetrics(
        conversations_today=conv_today.scalar() or 0,
        open_conversations=open_convs.scalar() or 0,
        messages_today=msgs_today.scalar() or 0,
        deals_open=deals_open.scalar() or 0,
        deals_won=deals_won.scalar() or 0,
        pipeline_value=float(pipeline_value_q.scalar() or 0),
        calls_today=calls_today.scalar() or 0,
        active_calls=active_calls.scalar() or 0,
        avg_call_duration_seconds=float(avg_duration_q.scalar() or 0),
        voice_spend_today_cents=float(spend_today_q.scalar() or 0),
        voice_answer_rate=answer_rate,
        outbound_calls_today=outbound_today_q.scalar() or 0,
    )
