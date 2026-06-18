"""Runtime query endpoints: /v1/query, /v1/search, optional /v1/answer."""
import time
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.ext.asyncio import AsyncSession

from voicerag.config import settings
from voicerag.core.api_key import get_kb_from_api_key
from voicerag.db.models import KnowledgeBase
from voicerag.db.session import get_session
from voicerag.embedding.embedder import get_embedder_instance
from voicerag.retrieval.retriever import retrieve
from voicerag.schemas.query import (
    QueryRequest, QueryResponse, SearchResponse,
    AnswerRequest, AnswerResponse, RetrievedChunkSchema,
)
from voicerag.vector.qdrant_store import get_qdrant_instance

router = APIRouter(prefix="/v1", tags=["query"])


def _to_chunk_schema(chunk) -> RetrievedChunkSchema:
    return RetrievedChunkSchema(
        text=chunk.text,
        score=chunk.score,
        document_id=chunk.document_id,
        chunk_index=chunk.chunk_index,
        filename=chunk.filename,
    )


@router.post("/query", response_model=QueryResponse)
async def query(
    body: QueryRequest,
    request: Request,
    kb: KnowledgeBase = Depends(get_kb_from_api_key),
    session: AsyncSession = Depends(get_session),
):
    top_k = body.top_k if body.top_k is not None else settings.default_top_k
    redis = getattr(request.app.state, "redis", None)
    qdrant = get_qdrant_instance()
    embedder = get_embedder_instance()

    api_key_id = getattr(request.state, "api_key_id", None)
    result = await retrieve(
        kb=kb,
        query=body.query,
        top_k=top_k,
        hybrid=body.hybrid,
        redis=redis,
        qdrant=qdrant,
        embedder=embedder,
        api_key_id=api_key_id,
    )

    return QueryResponse(
        context=result.context,
        chunks=[_to_chunk_schema(c) for c in result.chunks],
        cache_hit=result.cache_hit,
        latency_ms=result.latency_ms,
        top_score=result.top_score,
    )


@router.post("/search", response_model=SearchResponse)
async def search(
    body: QueryRequest,
    request: Request,
    kb: KnowledgeBase = Depends(get_kb_from_api_key),
    session: AsyncSession = Depends(get_session),
):
    """Same as /query but returns only chunks (no assembled context)."""
    top_k = body.top_k if body.top_k is not None else settings.default_top_k
    redis = getattr(request.app.state, "redis", None)
    qdrant = get_qdrant_instance()
    embedder = get_embedder_instance()
    api_key_id = getattr(request.state, "api_key_id", None)

    result = await retrieve(
        kb=kb,
        query=body.query,
        top_k=top_k,
        hybrid=body.hybrid,
        redis=redis,
        qdrant=qdrant,
        embedder=embedder,
        api_key_id=api_key_id,
    )

    return SearchResponse(
        chunks=[_to_chunk_schema(c) for c in result.chunks],
        cache_hit=result.cache_hit,
        latency_ms=result.latency_ms,
        top_score=result.top_score,
    )


if settings.enable_answer_endpoint:
    @router.post("/answer", response_model=AnswerResponse)
    async def answer(
        body: AnswerRequest,
        request: Request,
        kb: KnowledgeBase = Depends(get_kb_from_api_key),
        session: AsyncSession = Depends(get_session),
    ):
        """Optional LLM-based answer generation (Groq). Only mounted when enable_answer_endpoint=True."""
        import time as _time
        t0 = _time.perf_counter()

        top_k = body.top_k if body.top_k is not None else settings.default_top_k
        redis = getattr(request.app.state, "redis", None)
        qdrant = get_qdrant_instance()
        embedder = get_embedder_instance()
        api_key_id = getattr(request.state, "api_key_id", None)

        result = await retrieve(
            kb=kb,
            query=body.query,
            top_k=top_k,
            hybrid=None,
            redis=redis,
            qdrant=qdrant,
            embedder=embedder,
            api_key_id=api_key_id,
        )

        if not settings.groq_api_key:
            raise HTTPException(status_code=500, detail="Groq API key not configured")

        from groq import AsyncGroq
        client = AsyncGroq(api_key=settings.groq_api_key)

        system_prompt = body.system_prompt or (
            "You are a helpful voice assistant. Answer the question concisely "
            "using only the provided context. Keep the answer short and speakable."
        )
        user_message = f"Context:\n{result.context}\n\nQuestion: {body.query}"

        completion = await client.chat.completions.create(
            model=settings.groq_model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message},
            ],
            max_tokens=256,
        )

        answer_text = completion.choices[0].message.content or ""
        latency_ms = (_time.perf_counter() - t0) * 1000

        return AnswerResponse(
            answer=answer_text,
            chunks=[_to_chunk_schema(c) for c in result.chunks],
            latency_ms=latency_ms,
        )
