"""Voice & Telephony settings — read current config, expose test endpoint."""
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Optional

from voiceagent.api.auth import get_current_user
from voiceagent.config import settings
from voiceagent.db.models import User

router = APIRouter(prefix="/settings/voice", tags=["settings"])


class VoiceTelephonyInfo(BaseModel):
    """Read-only current telephony configuration (secrets masked)."""
    # SIP / Telephony
    sip_from_number: str
    sip_provider_uri: str
    sip_auth_username: str
    sip_trunk_id: str
    # Services
    deepgram_model: str
    groq_model: str
    cartesia_model: str
    # Cost per minute (cents)
    cost_stt_cpm: float
    cost_llm_cpm: float
    cost_tts_cpm: float
    cost_telephony_cpm: float
    cost_total_cpm: float
    # LiveKit
    livekit_url: str
    # Status flags
    sip_configured: bool
    livekit_configured: bool


class CostBreakdown(BaseModel):
    component: str
    label: str
    cpm_cents: float
    cpm_dollars: float


@router.get("", response_model=VoiceTelephonyInfo)
async def get_voice_settings(current_user: User = Depends(get_current_user)):
    """Return the current voice infrastructure config (read from .env)."""
    total = settings.cost_stt_cpm + settings.cost_llm_cpm + settings.cost_tts_cpm + settings.cost_telephony_cpm
    return VoiceTelephonyInfo(
        sip_from_number=settings.sip_from_number or "(not set)",
        sip_provider_uri=settings.sip_provider_uri or "(not set)",
        sip_auth_username=settings.sip_auth_username or "(not set)",
        sip_trunk_id=settings.livekit_sip_trunk_id or "(not set)",
        deepgram_model=settings.deepgram_model,
        groq_model=settings.groq_model,
        cartesia_model=settings.cartesia_model,
        cost_stt_cpm=settings.cost_stt_cpm,
        cost_llm_cpm=settings.cost_llm_cpm,
        cost_tts_cpm=settings.cost_tts_cpm,
        cost_telephony_cpm=settings.cost_telephony_cpm,
        cost_total_cpm=round(total, 2),
        livekit_url=settings.livekit_url,
        sip_configured=bool(settings.sip_from_number and settings.sip_provider_uri),
        livekit_configured=bool(settings.livekit_url and settings.livekit_api_key),
    )


@router.get("/costs", response_model=list[CostBreakdown])
async def get_cost_breakdown(current_user: User = Depends(get_current_user)):
    """Itemized cost breakdown per minute."""
    return [
        CostBreakdown(component="stt",       label="Speech-to-Text (Deepgram)",   cpm_cents=settings.cost_stt_cpm,       cpm_dollars=settings.cost_stt_cpm / 100),
        CostBreakdown(component="llm",       label="Language Model (Groq)",        cpm_cents=settings.cost_llm_cpm,       cpm_dollars=settings.cost_llm_cpm / 100),
        CostBreakdown(component="tts",       label="Text-to-Speech (Cartesia)",    cpm_cents=settings.cost_tts_cpm,       cpm_dollars=settings.cost_tts_cpm / 100),
        CostBreakdown(component="telephony", label="Telephony (SIP/Telnyx)",       cpm_cents=settings.cost_telephony_cpm, cpm_dollars=settings.cost_telephony_cpm / 100),
    ]
