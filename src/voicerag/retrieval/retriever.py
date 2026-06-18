"""Retrieval: cache -> embed -> qdrant search -> assemble context."""
import asyncio
import hashlib
import json
import time
from dataclasses import dataclass
from typing import Optional

from voicerag.config import settings
from voicerag.db.models import KnowledgeBase, QueryLog
from voicerag.db.session import AsyncSessionLocal
from voicerag.embedding.embedder import Embedder
from voicerag.vector.qdrant_store import QdrantStore

MAX_CONTEXT_CHARS = 4000


@dataclass
class RetrievedChunk:
    text: str
    score: float
    document_id: str
    chunk_index: int
    filename: str


@dataclass
class RetrievalResult:
    chunks: list[RetrievedChunk]
    context: str
    cache_hit: bool
    latency_ms: float
    top_score: Optional[float]


def _normalize_query(query: str) -> str:
    return " ".join(query.strip().split())


def _cache_key(kb_id: str, query: str, top_k: int, hybrid: bool) -> str:
    q_hash = hashlib.sha256(query.encode()).hexdigest()
    return f"q:{kb_id}:{q_hash}:{top_k}:{int(hybrid)}"


def _assemble_context(chunks: list[RetrievedChunk]) -> str:
    parts = []
    total = 0
    for chunk in chunks:
        prefix = f"[Source: {chunk.filename}]\n" if chunk.filename else ""
        segment = f"{prefix}{chunk.text}"
        if total + len(segment) > MAX_CONTEXT_CHARS:
            remaining = MAX_CONTEXT_CHARS - total
            if remaining > 50:
                parts.append(segment[:remaining])
            break
        parts.append(segment)
        total += len(segment) + 8  # separator length
    return "\n\n---\n\n".join(parts)


async def retrieve(
    kb: KnowledgeBase,
    query: str,
    top_k: int,
    hybrid: Optional[bool],
    redis,
    qdrant: QdrantStore,
    embedder: Embedder,
    api_key_id: Optional[str] = None,
) -> RetrievalResult:
    """
    Main retrieval function.
    1. Check Redis cache.
    2. Embed + Qdrant search.
    3. Assemble context.
    4. Cache result.
    5. Log async.
    """
    if not query or not query.strip():
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Query must not be empty")

    # Clamp top_k
    top_k = max(1, min(top_k, settings.max_top_k))

    normalized = _normalize_query(query)

    # Resolve hybrid flag
    use_hybrid = hybrid if hybrid is not None else kb.enable_hybrid
    use_hybrid = use_hybrid and settings.enable_hybrid

    t_start = time.perf_counter()

    # 1. Cache lookup
    cache_key = _cache_key(kb.id, normalized, top_k, use_hybrid)
    if redis:
        cached = await redis.get(cache_key)
        if cached:
            data = json.loads(cached.decode())
            latency_ms = (time.perf_counter() - t_start) * 1000
            result = RetrievalResult(
                chunks=[RetrievedChunk(**c) for c in data["chunks"]],
                context=data["context"],
                cache_hit=True,
                latency_ms=latency_ms,
                top_score=data.get("top_score"),
            )
            _fire_log(kb.id, api_key_id, query, result)
            return result

    # 2. Embed
    dense_vec = embedder.embed_query(normalized)
    sparse_vec = None
    if use_hybrid:
        sparse_vecs = embedder.embed_sparse([normalized])
        sparse_vec = sparse_vecs[0] if sparse_vecs else None

    # 3. Qdrant search
    hits = await qdrant.search(
        collection_name=kb.collection_name,
        dense_vec=dense_vec,
        sparse_vec=sparse_vec,
        top_k=top_k,
        score_threshold=settings.min_score_threshold,
        hybrid=use_hybrid,
    )

    # 4. Build result
    chunks = []
    for h in hits:
        chunks.append(RetrievedChunk(
            text=h.payload.get("text", ""),
            score=h.score,
            document_id=h.payload.get("document_id", ""),
            chunk_index=h.payload.get("chunk_index", 0),
            filename=h.payload.get("filename", ""),
        ))

    context = _assemble_context(chunks) if chunks else ""
    top_score = chunks[0].score if chunks else None
    latency_ms = (time.perf_counter() - t_start) * 1000

    result = RetrievalResult(
        chunks=chunks,
        context=context,
        cache_hit=False,
        latency_ms=latency_ms,
        top_score=top_score,
    )

    # 5. Cache
    if redis:
        cache_data = {
            "chunks": [
                {
                    "text": c.text,
                    "score": c.score,
                    "document_id": c.document_id,
                    "chunk_index": c.chunk_index,
                    "filename": c.filename,
                }
                for c in chunks
            ],
            "context": context,
            "top_score": top_score,
        }
        await redis.set(cache_key, json.dumps(cache_data), ex=settings.query_cache_ttl_seconds)

    # 6. Log async (fire-and-forget)
    _fire_log(kb.id, api_key_id, query, result)

    return result


def _fire_log(
    kb_id: str,
    api_key_id: Optional[str],
    query_text: str,
    result: RetrievalResult,
) -> None:
    """Fire-and-forget query log insert in a separate session."""
    asyncio.create_task(_insert_log(kb_id, api_key_id, query_text, result))


async def _insert_log(
    kb_id: str,
    api_key_id: Optional[str],
    query_text: str,
    result: RetrievalResult,
) -> None:
    try:
        async with AsyncSessionLocal() as session:
            log = QueryLog(
                knowledge_base_id=kb_id,
                api_key_id=api_key_id,
                query_text=query_text,
                top_score=result.top_score,
                latency_ms=result.latency_ms,
                cache_hit=result.cache_hit,
                result_count=len(result.chunks),
            )
            session.add(log)
            await session.commit()
    except Exception:
        pass
