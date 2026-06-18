"""QdrantStore: async wrapper around qdrant-client for collection management and search."""
import uuid
from dataclasses import dataclass
from typing import Optional

from qdrant_client import AsyncQdrantClient
from qdrant_client.models import (
    Distance,
    VectorParams,
    SparseVectorParams,
    SparseIndexParams,
    PointStruct,
    SparseVector as QdrantSparseVector,
    Filter,
    FieldCondition,
    MatchValue,
    Prefetch,
    FusionQuery,
    Fusion,
)

from voicerag.config import settings
from voicerag.embedding.embedder import SparseVector


@dataclass
class Hit:
    id: str
    score: float
    payload: dict


def _point_id(document_id: str, chunk_index: int) -> str:
    """Deterministic uuid5 from document_id:chunk_index — idempotent re-ingest."""
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"{document_id}:{chunk_index}"))


class QdrantStore:
    def __init__(self):
        self._client: Optional[AsyncQdrantClient] = None

    async def init(self) -> None:
        self._client = AsyncQdrantClient(
            url=settings.qdrant_url,
            api_key=settings.qdrant_api_key or None,
            prefer_grpc=settings.qdrant_prefer_grpc,
        )

    async def close(self) -> None:
        if self._client:
            await self._client.close()

    async def ping(self) -> bool:
        """Health check — list collections."""
        await self._client.get_collections()
        return True

    async def ensure_collection(
        self,
        collection_name: str,
        dim: int,
        hybrid: bool,
    ) -> None:
        """Create collection if it does not exist."""
        existing = await self._client.collection_exists(collection_name)
        if existing:
            return

        if hybrid:
            # Named vectors: dense + sparse
            await self._client.create_collection(
                collection_name=collection_name,
                vectors_config={
                    "dense": VectorParams(
                        size=dim,
                        distance=Distance.COSINE,
                        on_disk=False,
                    )
                },
                sparse_vectors_config={
                    "sparse": SparseVectorParams(
                        index=SparseIndexParams(on_disk=False)
                    )
                },
            )
        else:
            await self._client.create_collection(
                collection_name=collection_name,
                vectors_config=VectorParams(
                    size=dim,
                    distance=Distance.COSINE,
                    on_disk=False,
                ),
            )

    async def upsert(
        self,
        collection_name: str,
        points: list[dict],
        hybrid: bool = False,
    ) -> None:
        """
        Upsert points. Each point dict has:
          - document_id, chunk_index, text, knowledge_base_id
          - dense_vector: list[float]
          - sparse_vector: SparseVector (optional, for hybrid)
        """
        qdrant_points = []
        for p in points:
            point_id = _point_id(p["document_id"], p["chunk_index"])
            payload = {
                "document_id": p["document_id"],
                "chunk_index": p["chunk_index"],
                "text": p["text"],
                "knowledge_base_id": p["knowledge_base_id"],
                "filename": p.get("filename", ""),
            }
            if hybrid and p.get("sparse_vector"):
                sv: SparseVector = p["sparse_vector"]
                qdrant_points.append(
                    PointStruct(
                        id=point_id,
                        payload=payload,
                        vector={
                            "dense": p["dense_vector"],
                            "sparse": QdrantSparseVector(
                                indices=sv.indices,
                                values=sv.values,
                            ),
                        },
                    )
                )
            else:
                qdrant_points.append(
                    PointStruct(
                        id=point_id,
                        payload=payload,
                        vector=p["dense_vector"],
                    )
                )

        if qdrant_points:
            await self._client.upsert(
                collection_name=collection_name,
                points=qdrant_points,
            )

    async def search(
        self,
        collection_name: str,
        dense_vec: list[float],
        sparse_vec: Optional[SparseVector],
        top_k: int,
        score_threshold: float,
        hybrid: bool = False,
    ) -> list[Hit]:
        """
        Dense-only or hybrid (RRF fusion) search.
        Returns list[Hit] sorted by score descending.
        """
        if hybrid and sparse_vec:
            # Use Query API with prefetch + RRF fusion
            results = await self._client.query_points(
                collection_name=collection_name,
                prefetch=[
                    Prefetch(
                        query=dense_vec,
                        using="dense",
                        limit=top_k * 2,
                    ),
                    Prefetch(
                        query=QdrantSparseVector(
                            indices=sparse_vec.indices,
                            values=sparse_vec.values,
                        ),
                        using="sparse",
                        limit=top_k * 2,
                    ),
                ],
                query=FusionQuery(fusion=Fusion.RRF),
                limit=top_k,
                # RRF scores are ~1/(k+rank), not cosine — applying a cosine threshold
                # here would silently drop all results. Threshold only applies to dense path.
                with_payload=True,
            )
            points = results.points
        elif hybrid:
            # Hybrid collection but no sparse vec — dense-only via named vector
            results = await self._client.search(
                collection_name=collection_name,
                query_vector=("dense", dense_vec),
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
            )
            points = results
        else:
            # Non-hybrid collection — plain vector
            results = await self._client.search(
                collection_name=collection_name,
                query_vector=dense_vec,
                limit=top_k,
                score_threshold=score_threshold,
                with_payload=True,
            )
            points = results

        hits = []
        for p in points:
            hits.append(Hit(
                id=str(p.id),
                score=p.score,
                payload=p.payload or {},
            ))
        return hits

    async def delete_by_document(
        self,
        collection_name: str,
        document_id: str,
    ) -> None:
        """Delete all points with payload.document_id == document_id."""
        await self._client.delete(
            collection_name=collection_name,
            points_selector=Filter(
                must=[
                    FieldCondition(
                        key="document_id",
                        match=MatchValue(value=document_id),
                    )
                ]
            ),
        )

    async def drop_collection(self, collection_name: str) -> None:
        """Drop the entire collection (on KB delete)."""
        exists = await self._client.collection_exists(collection_name)
        if exists:
            await self._client.delete_collection(collection_name)


# Module-level singleton (set at startup)
_qdrant_instance: Optional[QdrantStore] = None


def get_qdrant_instance() -> QdrantStore:
    if _qdrant_instance is None:
        raise RuntimeError("QdrantStore not initialized")
    return _qdrant_instance


def set_qdrant_instance(store: QdrantStore) -> None:
    global _qdrant_instance
    _qdrant_instance = store
