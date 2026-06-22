"""Ingestion pipeline: load -> chunk -> embed -> upsert -> mark status."""
import asyncio
from datetime import datetime, UTC

from voicerag.config import settings
from voicerag.db.models import Document, KnowledgeBase, DocStatus, SourceType
from voicerag.db.session import AsyncSessionLocal
from voicerag.embedding.embedder import get_embedder_instance
from voicerag.vector.qdrant_store import get_qdrant_instance
from voicerag.ingestion import loaders, chunker


async def ingest_document(document_id: str) -> None:
    """
    Background task: load -> chunk -> embed -> upsert -> mark ready/failed.
    Never raises — all failures are caught and stored in document.error.
    """
    async with AsyncSessionLocal() as session:
        try:
            doc: Document = await session.get(Document, document_id)
            if not doc:
                return

            # Mark processing
            doc.status = DocStatus.processing
            doc.updated_at = datetime.now(UTC)
            await session.commit()

            # Load text
            text = await _load_text(doc)

            # Chunk
            chunks = chunker.chunk_text(text)
            if not chunks:
                raise ValueError("no extractable text")

            # Get KB
            kb: KnowledgeBase = await session.get(KnowledgeBase, doc.knowledge_base_id)
            if not kb:
                raise ValueError("knowledge base not found")

            embedder = get_embedder_instance()
            qdrant = get_qdrant_instance()

            # Ensure collection exists
            await qdrant.ensure_collection(
                kb.collection_name,
                dim=settings.embedding_dim,
                hybrid=kb.enable_hybrid,
            )

            # Embed and upsert in batches of 64
            batch_size = 64
            total_chunks = len(chunks)

            for batch_start in range(0, total_chunks, batch_size):
                batch = chunks[batch_start: batch_start + batch_size]
                texts = [c.text for c in batch]

                # Offload CPU-bound embedding to a thread so it never blocks the
                # event loop (which also runs live voice calls in this process).
                dense_vecs = await asyncio.to_thread(embedder.embed_documents, texts)
                sparse_vecs = None
                if kb.enable_hybrid and settings.enable_hybrid:
                    sparse_vecs = await asyncio.to_thread(embedder.embed_sparse, texts)

                points = []
                for j, c in enumerate(batch):
                    p = {
                        "document_id": doc.id,
                        "chunk_index": c.index,
                        "text": c.text,
                        "knowledge_base_id": kb.id,
                        "filename": doc.filename or "",
                        "dense_vector": dense_vecs[j],
                    }
                    if sparse_vecs:
                        p["sparse_vector"] = sparse_vecs[j]
                    points.append(p)

                await qdrant.upsert(
                    kb.collection_name,
                    points,
                    hybrid=kb.enable_hybrid and settings.enable_hybrid,
                )

            # Update document
            doc.status = DocStatus.ready
            doc.chunk_count = total_chunks
            doc.updated_at = datetime.now(UTC)

            # Increment KB counters
            kb.doc_count = (kb.doc_count or 0) + 1
            kb.chunk_count = (kb.chunk_count or 0) + total_chunks
            kb.updated_at = datetime.now(UTC)

            await session.commit()

        except Exception as exc:
            try:
                doc = await session.get(Document, document_id)
                if doc:
                    doc.status = DocStatus.failed
                    doc.error = str(exc)
                    doc.updated_at = datetime.now(UTC)
                    await session.commit()
            except Exception:
                pass


async def _load_text(doc: Document) -> str:
    """Dispatch to correct loader based on source_type."""
    if doc.source_type == SourceType.pdf:
        return loaders.load_pdf(doc.storage_path)
    elif doc.source_type == SourceType.docx:
        return loaders.load_docx(doc.storage_path)
    elif doc.source_type == SourceType.txt:
        return loaders.load_txt(doc.storage_path)
    elif doc.source_type == SourceType.url:
        return await loaders.load_url(doc.source_url)
    else:
        raise ValueError(f"Unknown source_type: {doc.source_type}")
