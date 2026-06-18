"""Singleton Embedder: dense + sparse (BM25) via fastembed."""
from typing import Optional

from voicerag.config import settings


class SparseVector:
    """Simple container for sparse vector (indices + values)."""

    def __init__(self, indices: list[int], values: list[float]):
        self.indices = indices
        self.values = values


class Embedder:
    """
    Wraps fastembed TextEmbedding (dense) and SparseTextEmbedding (BM25).
    Constructed once at startup and warmed with a dummy embed.
    """

    def __init__(self):
        from fastembed import TextEmbedding, SparseTextEmbedding

        self._dense_model = TextEmbedding(model_name=settings.embedding_model)
        self._sparse_model: Optional[SparseTextEmbedding] = None
        if settings.enable_hybrid:
            self._sparse_model = SparseTextEmbedding(model_name=settings.sparse_model)

        # Warm the models (first call compiles/loads ONNX graph)
        self._warm()

    def _warm(self) -> None:
        _ = list(self._dense_model.embed(["warm up"]))
        if self._sparse_model:
            _ = list(self._sparse_model.embed(["warm up"]))

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def _validate(self, text: str) -> None:
        if not text or not text.strip():
            raise ValueError("Text must be non-empty")

    def embed_query(self, text: str) -> list[float]:
        self._validate(text)
        vectors = list(self._dense_model.embed([text]))
        return vectors[0].tolist()

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        for t in texts:
            self._validate(t)
        return [v.tolist() for v in self._dense_model.embed(texts)]

    def embed_sparse(self, texts: list[str]) -> list[SparseVector]:
        if not self._sparse_model:
            raise RuntimeError("Sparse model not enabled (enable_hybrid=False)")
        if not texts:
            return []
        result = []
        for sv in self._sparse_model.embed(texts):
            result.append(SparseVector(
                indices=sv.indices.tolist(),
                values=sv.values.tolist(),
            ))
        return result


# Module-level singleton (set at startup via lifespan)
_embedder_instance: Optional[Embedder] = None


def get_embedder_instance() -> Embedder:
    if _embedder_instance is None:
        raise RuntimeError("Embedder not initialized")
    return _embedder_instance


def set_embedder_instance(emb: Embedder) -> None:
    global _embedder_instance
    _embedder_instance = emb
