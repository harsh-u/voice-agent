from datetime import datetime, UTC
from typing import Optional
import uuid

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voiceagent.config import settings
from voiceagent.db.models import AgentConfig
from voiceagent.db.session import get_session

router = APIRouter(prefix="/agents", tags=["agents"])


# ---------------------------------------------------------------------------
# Pydantic schemas
# ---------------------------------------------------------------------------


class AgentConfigCreate(BaseModel):
    name: str
    system_prompt: str
    voice_id: Optional[str] = None
    llm_model: Optional[str] = None
    tools_json: Optional[str] = None
    sip_trunk_id: Optional[str] = None
    rag_api_key: Optional[str] = None
    rag_kb_id: Optional[str] = None


class AgentConfigUpdate(BaseModel):
    name: Optional[str] = None
    system_prompt: Optional[str] = None
    voice_id: Optional[str] = None
    llm_model: Optional[str] = None
    tools_json: Optional[str] = None
    sip_trunk_id: Optional[str] = None
    rag_api_key: Optional[str] = None
    rag_kb_id: Optional[str] = None


class AgentConfigResponse(BaseModel):
    id: str
    name: str
    system_prompt: Optional[str]
    voice_id: Optional[str]
    llm_model: Optional[str]
    tools_json: Optional[str]
    sip_trunk_id: Optional[str]
    rag_api_key: Optional[str]
    rag_kb_id: Optional[str]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[AgentConfigResponse])
async def list_agents(session: AsyncSession = Depends(get_session)):
    """Return all agent configurations."""
    result = await session.execute(select(AgentConfig))
    return result.scalars().all()


@router.post("", response_model=AgentConfigResponse, status_code=201)
async def create_agent(
    body: AgentConfigCreate,
    session: AsyncSession = Depends(get_session),
):
    """Create a new agent configuration."""
    now = datetime.now(UTC)
    agent = AgentConfig(
        id=str(uuid.uuid4()),
        name=body.name,
        system_prompt=body.system_prompt,
        voice_id=body.voice_id or settings.cartesia_voice_id,
        llm_model=body.llm_model or settings.groq_model,
        tools_json=body.tools_json,
        sip_trunk_id=body.sip_trunk_id or settings.livekit_sip_trunk_id,
        rag_api_key=body.rag_api_key,
        rag_kb_id=body.rag_kb_id,
        created_at=now,
        updated_at=now,
    )
    session.add(agent)
    await session.commit()
    await session.refresh(agent)
    return agent


@router.get("/options/voices")
async def list_voices():
    """Return the supported TTS voices with display names."""
    return [
        {"id": "a0e99841-438c-4a64-b679-ae501e7d6091", "name": "Default (Cartesia Sonic)", "provider": "cartesia"},
        {"id": "5345cf08-6f37-424d-a5d9-8ae1101b9377", "name": "Calm Professional", "provider": "cartesia"},
        {"id": "79a125e8-cd45-4c13-8a67-188112f4dd22", "name": "Energetic", "provider": "cartesia"},
        {"id": "156fb8d2-335b-4950-9cb3-a2d33befec77", "name": "Warm & Friendly", "provider": "cartesia"},
    ]


@router.get("/options/models")
async def list_models():
    """Return the supported LLM models with cost/latency characteristics."""
    return [
        {
            "id": "llama-3.1-8b-instant",
            "name": "Llama 3.1 8B (Fast & Cheap)",
            "provider": "groq",
            "ttft_ms": 120,
            "cost_per_min_cents": 0.40,
            "recommended": True,
        },
        {
            "id": "llama-3.3-70b-versatile",
            "name": "Llama 3.3 70B (High Quality)",
            "provider": "groq",
            "ttft_ms": 200,
            "cost_per_min_cents": 1.80,
            "recommended": False,
        },
    ]


@router.get("/{agent_id}", response_model=AgentConfigResponse)
async def get_agent(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Fetch a single agent configuration by ID."""
    agent = await session.get(AgentConfig, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    return agent


@router.patch("/{agent_id}", response_model=AgentConfigResponse)
async def update_agent(
    agent_id: str,
    body: AgentConfigUpdate,
    session: AsyncSession = Depends(get_session),
):
    """Partially update an agent configuration. Omitted fields are not changed."""
    agent = await session.get(AgentConfig, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")

    update_data = body.model_dump(exclude_unset=True)
    for field, value in update_data.items():
        setattr(agent, field, value)
    agent.updated_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(agent)
    return agent


@router.delete("/{agent_id}", status_code=204)
async def delete_agent(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
):
    """Delete an agent configuration."""
    agent = await session.get(AgentConfig, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    await session.delete(agent)
    await session.commit()
