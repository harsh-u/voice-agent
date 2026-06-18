"""CRUD for projects + project ingest API keys."""
from __future__ import annotations
from datetime import datetime, UTC
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voxscope.core.api_key import generate_api_key, hash_api_key
from voxscope.core.security import get_current_user
from voxscope.db.models import ApiKey, Project, User
from voxscope.db.session import get_session

router = APIRouter(prefix="/v1/projects", tags=["projects"])


# ---------------------------------------------------------------------------
# Request / response schemas (local to this module; simple enough to inline)
# ---------------------------------------------------------------------------

class ProjectCreate(BaseModel):
    name: str
    sample_rate: float = 1.0
    slow_threshold_ms: float = 800.0


class ProjectResponse(BaseModel):
    id: str
    user_id: str
    name: str
    sample_rate: float
    slow_threshold_ms: float
    created_at: datetime

    model_config = {"from_attributes": True}


class ProjectCreatedResponse(ProjectResponse):
    """Returned once on creation — includes the full ingest API key (shown once)."""
    ingest_key: str


class ApiKeyResponse(BaseModel):
    id: str
    key_prefix: str
    name: Optional[str]
    last_used_at: Optional[datetime]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyCreatedResponse(ApiKeyResponse):
    key: str  # shown once


class ApiKeyCreate(BaseModel):
    name: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_owned_project(
    project_id: str,
    current_user: User,
    session: AsyncSession,
) -> Project:
    project = await session.get(Project, project_id)
    if not project:
        raise HTTPException(status_code=404, detail="Project not found")
    if project.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return project


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("", response_model=ProjectCreatedResponse, status_code=201)
async def create_project(
    body: ProjectCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = Project(
        user_id=current_user.id,
        name=body.name,
        sample_rate=body.sample_rate,
        slow_threshold_ms=body.slow_threshold_ms,
    )
    session.add(project)
    await session.flush()  # get project.id

    # Auto-create the first ingest API key
    full_key = generate_api_key()
    hashed = hash_api_key(full_key)
    prefix = full_key[:12]

    api_key = ApiKey(
        project_id=project.id,
        user_id=current_user.id,
        key_prefix=prefix,
        hashed_key=hashed,
        name="default",
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(project)

    return ProjectCreatedResponse(
        id=project.id,
        user_id=project.user_id,
        name=project.name,
        sample_rate=project.sample_rate,
        slow_threshold_ms=project.slow_threshold_ms,
        created_at=project.created_at,
        ingest_key=full_key,
    )


@router.get("", response_model=list[ProjectResponse])
async def list_projects(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Project).where(Project.user_id == current_user.id)
    )
    return result.scalars().all()


@router.get("/{project_id}", response_model=ProjectResponse)
async def get_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await _get_owned_project(project_id, current_user, session)


@router.delete("/{project_id}", status_code=204)
async def delete_project(
    project_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _get_owned_project(project_id, current_user, session)
    await session.delete(project)
    await session.commit()


# ---------------------------------------------------------------------------
# Project API key management
# ---------------------------------------------------------------------------

@router.post("/{project_id}/api-keys", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    project_id: str,
    body: ApiKeyCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _get_owned_project(project_id, current_user, session)

    full_key = generate_api_key()
    hashed = hash_api_key(full_key)
    prefix = full_key[:12]

    api_key = ApiKey(
        project_id=project.id,
        user_id=current_user.id,
        key_prefix=prefix,
        hashed_key=hashed,
        name=body.name,
    )
    session.add(api_key)
    await session.commit()
    await session.refresh(api_key)

    return ApiKeyCreatedResponse(
        id=api_key.id,
        key_prefix=prefix,
        name=api_key.name,
        last_used_at=api_key.last_used_at,
        is_active=api_key.is_active,
        created_at=api_key.created_at,
        key=full_key,
    )


@router.get("/{project_id}/api-keys", response_model=list[ApiKeyResponse])
async def list_api_keys(
    project_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _get_owned_project(project_id, current_user, session)
    result = await session.execute(
        select(ApiKey).where(ApiKey.project_id == project.id)
    )
    return result.scalars().all()


@router.delete("/{project_id}/api-keys/{key_id}", status_code=204)
async def revoke_api_key(
    project_id: str,
    key_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    project = await _get_owned_project(project_id, current_user, session)
    api_key = await session.get(ApiKey, key_id)
    if not api_key or api_key.project_id != project.id:
        raise HTTPException(status_code=404, detail="API key not found")
    api_key.is_active = False
    await session.commit()
