"""CRUD for knowledge bases."""
from datetime import datetime, UTC

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, delete
from sqlalchemy.ext.asyncio import AsyncSession

from voicerag.config import settings
from voicerag.core.security import get_current_user
from voicerag.db.models import KnowledgeBase, User, Document, ApiKey, QueryLog
from voicerag.db.session import get_session
from voicerag.schemas.knowledge_base import (
    KnowledgeBaseCreate, KnowledgeBaseUpdate, KnowledgeBaseResponse,
)
from voicerag.vector.qdrant_store import get_qdrant_instance

import os
import shutil

router = APIRouter(prefix="/knowledge-bases", tags=["knowledge-bases"])


def _collection_name(kb_id: str) -> str:
    return f"kb_{kb_id}"


@router.post("", response_model=KnowledgeBaseResponse, status_code=201)
async def create_kb(
    body: KnowledgeBaseCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    import uuid
    kb_id = str(uuid.uuid4())
    collection = _collection_name(kb_id)

    kb = KnowledgeBase(
        id=kb_id,
        user_id=current_user.id,
        name=body.name,
        description=body.description,
        collection_name=collection,
        embedding_model=settings.embedding_model,
        enable_hybrid=body.enable_hybrid if body.enable_hybrid is not None else True,
    )
    session.add(kb)
    await session.commit()
    await session.refresh(kb)

    # Create Qdrant collection
    try:
        qdrant = get_qdrant_instance()
        await qdrant.ensure_collection(
            collection,
            dim=settings.embedding_dim,
            hybrid=kb.enable_hybrid,
        )
    except Exception as exc:
        # Don't fail the API call; collection will be created on first ingest
        pass

    return kb


@router.get("", response_model=list[KnowledgeBaseResponse])
async def list_kbs(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(KnowledgeBase).where(KnowledgeBase.user_id == current_user.id)
    )
    return result.scalars().all()


@router.get("/{kb_id}", response_model=KnowledgeBaseResponse)
async def get_kb(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if kb.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return kb


@router.patch("/{kb_id}", response_model=KnowledgeBaseResponse)
async def update_kb(
    kb_id: str,
    body: KnowledgeBaseUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if kb.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    if body.name is not None:
        kb.name = body.name
    if body.description is not None:
        kb.description = body.description
    if body.enable_hybrid is not None:
        kb.enable_hybrid = body.enable_hybrid
    kb.updated_at = datetime.now(UTC)

    await session.commit()
    await session.refresh(kb)
    return kb


@router.delete("/{kb_id}", status_code=204)
async def delete_kb(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if kb.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")

    # Drop Qdrant collection
    try:
        qdrant = get_qdrant_instance()
        await qdrant.drop_collection(kb.collection_name)
    except Exception:
        pass

    # Delete storage files
    storage_dir = os.path.join("storage", kb_id)
    if os.path.exists(storage_dir):
        shutil.rmtree(storage_dir, ignore_errors=True)

    # Cascade-delete children in FK-safe order. query_logs must go first: its
    # knowledge_base_id is NOT NULL and it also references api_keys, so deleting
    # the KB/keys before the logs would violate the not-null FK constraint.
    await session.execute(
        delete(QueryLog).where(QueryLog.knowledge_base_id == kb_id)
    )
    await session.execute(
        delete(Document).where(Document.knowledge_base_id == kb_id)
    )
    await session.execute(
        delete(ApiKey).where(ApiKey.knowledge_base_id == kb_id)
    )

    await session.delete(kb)
    await session.commit()
