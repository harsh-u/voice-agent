"""Create / list / revoke API keys for a knowledge base."""
from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voicerag.core.api_key import generate_api_key, hash_api_key, invalidate_api_key_cache
from voicerag.core.security import get_current_user
from voicerag.db.models import ApiKey, KnowledgeBase, User
from voicerag.db.session import get_session
from voicerag.schemas.api_key import ApiKeyCreate, ApiKeyCreatedResponse, ApiKeyResponse

router = APIRouter(
    prefix="/knowledge-bases/{kb_id}/api-keys",
    tags=["api-keys"],
)


async def _get_owned_kb(
    kb_id: str,
    current_user: User,
    session: AsyncSession,
) -> KnowledgeBase:
    kb = await session.get(KnowledgeBase, kb_id)
    if not kb:
        raise HTTPException(status_code=404, detail="Knowledge base not found")
    if kb.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="Access denied")
    return kb


@router.post("", response_model=ApiKeyCreatedResponse, status_code=201)
async def create_api_key(
    kb_id: str,
    body: ApiKeyCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_owned_kb(kb_id, current_user, session)

    full_key = generate_api_key()
    hashed = hash_api_key(full_key)
    prefix = full_key[:12]

    api_key = ApiKey(
        knowledge_base_id=kb.id,
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
        name=api_key.name,
        key=full_key,
        key_prefix=prefix,
        created_at=api_key.created_at,
    )


@router.get("", response_model=list[ApiKeyResponse])
async def list_api_keys(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_owned_kb(kb_id, current_user, session)

    result = await session.execute(
        select(ApiKey).where(ApiKey.knowledge_base_id == kb.id)
    )
    return result.scalars().all()


@router.delete("/{key_id}", status_code=204)
async def revoke_api_key(
    kb_id: str,
    key_id: str,
    request: Request,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_owned_kb(kb_id, current_user, session)

    api_key = await session.get(ApiKey, key_id)
    if not api_key or api_key.knowledge_base_id != kb.id:
        raise HTTPException(status_code=404, detail="API key not found")

    api_key.is_active = False
    await session.commit()

    # Invalidate Redis cache
    await invalidate_api_key_cache(api_key.hashed_key, request)
