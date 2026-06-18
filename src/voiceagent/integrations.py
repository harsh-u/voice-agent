"""Unified-platform integration glue.

Bridges the merged RAG (``voicerag``) and observability (``voxscope``) engines
into the voice-agent so the whole thing is ONE product with ONE login.

Strategy (see MERGE_PLAN.md):

* The voice-agent ``User`` + JWT is the single identity. All three modules share
  ``JWT_SECRET`` and the same token shape (``{sub, kind:"access"}``), so a
  voice-agent access token is accepted by the ``/rag`` and ``/observability``
  routers via the FastAPI dependency overrides installed here — no second login.
* Agents are workspace-global (``AgentConfig`` has no ``user_id``), so RAG
  knowledge bases and observability traces are workspace-global too: every valid
  login maps to a single shared workspace account in the ``rag``/``obs`` schemas.
* Internal API keys (``vrag_`` / ``vsk_``) are managed server-side and never
  shown. The (separate-process) voice worker authenticates its telemetry with a
  deterministic ingest key derived from ``JWT_SECRET``.
"""
from __future__ import annotations

import hashlib

from fastapi import Depends, HTTPException, status
from jose import JWTError, jwt
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voiceagent.config import settings

# Fixed identity shared by all logins inside the rag/obs schemas.
WORKSPACE_USER_ID = "00000000-0000-0000-0000-000000000001"
WORKSPACE_EMAIL = "workspace@convoxio.local"
DEFAULT_PROJECT_ID = "00000000-0000-0000-0000-0000000000a1"
DEFAULT_PROJECT_NAME = "Voice Agent"


def default_ingest_key() -> str:
    """Deterministic ``vsk_`` ingest key derived from ``JWT_SECRET``.

    Lets the separate-process voice worker authenticate to the unified
    ``/observability/v1/ingest/batch`` endpoint without storing a plaintext key
    anywhere — both the provisioner (which stores its hash) and the worker
    (which sends it) can recompute the same value.
    """
    digest = hashlib.sha256(f"{settings.jwt_secret}:voxscope-default-ingest".encode()).hexdigest()
    return f"vsk_{digest}"


# ---------------------------------------------------------------------------
# Startup provisioning
# ---------------------------------------------------------------------------

async def provision_workspace() -> None:
    """Ensure the shared workspace rows exist in the rag + obs schemas (idempotent)."""
    await _provision_rag()
    await _provision_obs()


async def _provision_rag() -> None:
    from voicerag.db.session import AsyncSessionLocal as RagSession
    from voicerag.db.models import User as RagUser

    async with RagSession() as session:
        if await session.get(RagUser, WORKSPACE_USER_ID) is None:
            session.add(RagUser(
                id=WORKSPACE_USER_ID,
                email=WORKSPACE_EMAIL,
                hashed_password="!unified",  # login disabled; bridged via voiceagent JWT
            ))
            await session.commit()


async def _provision_obs() -> None:
    from voxscope.db.session import AsyncSessionLocal as ObsSession
    from voxscope.db.models import User as ObsUser, Project, ApiKey
    from voxscope.core.api_key import hash_api_key

    async with ObsSession() as session:
        if await session.get(ObsUser, WORKSPACE_USER_ID) is None:
            session.add(ObsUser(
                id=WORKSPACE_USER_ID,
                email=WORKSPACE_EMAIL,
                hashed_password="!unified",
            ))
            await session.flush()
        if await session.get(Project, DEFAULT_PROJECT_ID) is None:
            session.add(Project(
                id=DEFAULT_PROJECT_ID,
                user_id=WORKSPACE_USER_ID,
                name=DEFAULT_PROJECT_NAME,
            ))
            await session.flush()
        key = default_ingest_key()
        hashed = hash_api_key(key)
        existing = (await session.execute(
            select(ApiKey).where(ApiKey.hashed_key == hashed)
        )).scalar_one_or_none()
        if existing is None:
            session.add(ApiKey(
                project_id=DEFAULT_PROJECT_ID,
                user_id=WORKSPACE_USER_ID,
                key_prefix=key[:12],
                hashed_key=hashed,
                name="default-ingest (managed)",
            ))
        await session.commit()


# ---------------------------------------------------------------------------
# Auth bridge — dependency overrides for the mounted /rag and /observability routers
# ---------------------------------------------------------------------------

def _decode_access_token(token: str) -> str:
    """Validate a voice-agent JWT (shared secret) and return its subject."""
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
    except JWTError:
        raise exc
    if not payload.get("sub") or payload.get("kind") != "access":
        raise exc
    return payload["sub"]


# Imported here (not at module top) only the lightweight schemes/sessions.
from voicerag.core.security import oauth2_scheme as _rag_oauth2  # noqa: E402
from voicerag.db.session import get_session as _rag_get_session  # noqa: E402
from voxscope.core.security import oauth2_scheme as _obs_oauth2  # noqa: E402
from voxscope.db.session import get_session as _obs_get_session  # noqa: E402


async def rag_current_user(
    token: str = Depends(_rag_oauth2),
    session: AsyncSession = Depends(_rag_get_session),
):
    """Override for ``voicerag.core.security.get_current_user``.

    Accepts the voice-agent JWT and resolves it to the shared workspace RAG user.
    """
    from voicerag.db.models import User as RagUser

    _decode_access_token(token)
    user = await session.get(RagUser, WORKSPACE_USER_ID)
    if user is None:
        raise HTTPException(status_code=503, detail="RAG workspace not provisioned yet")
    return user


async def obs_current_user(
    token: str = Depends(_obs_oauth2),
    session: AsyncSession = Depends(_obs_get_session),
):
    """Override for ``voxscope.core.security.get_current_user``.

    Accepts the voice-agent JWT and resolves it to the shared workspace obs user.
    """
    from voxscope.db.models import User as ObsUser

    _decode_access_token(token)
    user = await session.get(ObsUser, WORKSPACE_USER_ID)
    if user is None:
        raise HTTPException(status_code=503, detail="Observability workspace not provisioned yet")
    return user


async def ensure_rag_key_for_kb(kb_id: str) -> str | None:
    """Mint a managed ``vrag_`` API key bound to ``kb_id`` and return the plaintext.

    Used when a knowledge base is attached to a voice agent: the voice pipeline
    authenticates to ``/rag/v1/query`` with this key (``AgentConfig.rag_api_key``).
    Keys are managed server-side and never shown to the user. Returns ``None`` if
    the knowledge base does not exist.
    """
    from voicerag.db.session import AsyncSessionLocal as RagSession
    from voicerag.db.models import ApiKey as RagApiKey, KnowledgeBase
    from voicerag.core.api_key import generate_api_key, hash_api_key

    async with RagSession() as session:
        kb = await session.get(KnowledgeBase, kb_id)
        if kb is None:
            return None
        key = generate_api_key()
        session.add(RagApiKey(
            knowledge_base_id=kb_id,
            user_id=WORKSPACE_USER_ID,
            key_prefix=key[:12],
            hashed_key=hash_api_key(key),
            name="agent (managed)",
        ))
        await session.commit()
        return key


def install_auth_bridge(app) -> None:
    """Wire the unified-auth overrides onto the FastAPI app."""
    from voicerag.core.security import get_current_user as rag_gcu
    from voxscope.core.security import get_current_user as obs_gcu

    app.dependency_overrides[rag_gcu] = rag_current_user
    app.dependency_overrides[obs_gcu] = obs_current_user
