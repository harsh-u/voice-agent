"""Document upload, URL ingest, list, get, delete."""
import ipaddress
import os
import socket
from pathlib import Path
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from voicerag.config import settings
from voicerag.core.security import get_current_user
from voicerag.db.models import Document, DocStatus, KnowledgeBase, Plan, SourceType, User
from voicerag.db.session import get_session
from voicerag.ingestion.pipeline import ingest_document
from voicerag.schemas.document import DocumentResponse, IngestUrlRequest
from voicerag.vector.qdrant_store import get_qdrant_instance
from datetime import datetime, UTC

# RFC-1918 + loopback + link-local ranges blocked for SSRF protection
_PRIVATE_NETWORKS = [
    ipaddress.ip_network("10.0.0.0/8"),
    ipaddress.ip_network("172.16.0.0/12"),
    ipaddress.ip_network("192.168.0.0/16"),
    ipaddress.ip_network("127.0.0.0/8"),
    ipaddress.ip_network("169.254.0.0/16"),
    ipaddress.ip_network("::1/128"),
    ipaddress.ip_network("fc00::/7"),
    ipaddress.ip_network("fe80::/10"),
]


def _check_url_safety(url: str) -> tuple[bool, str]:
    """Validate a URL for ingestion.

    Returns ``(ok, reason)``. ``reason`` is a human-readable explanation when
    ``ok`` is False, so callers can distinguish a bad scheme, an unresolvable
    host, and a private/internal address (previously all collapsed into one
    misleading message).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False, "URL must start with http:// or https://"
    hostname = parsed.hostname
    if not hostname:
        return False, "URL is missing a hostname"
    try:
        resolved = socket.gethostbyname(hostname)
    except OSError:
        return False, f"Could not resolve host '{hostname}' (DNS lookup failed or no network access)"
    try:
        ip = ipaddress.ip_address(resolved)
    except ValueError:
        return False, f"Host '{hostname}' resolved to an invalid address"
    if any(ip in net for net in _PRIVATE_NETWORKS):
        return False, "URL resolves to a private/internal address, which is not allowed"
    return True, ""


def _is_ssrf_safe(url: str) -> bool:
    """Backwards-compatible boolean wrapper around :func:`_check_url_safety`."""
    return _check_url_safety(url)[0]

router = APIRouter(
    prefix="/knowledge-bases/{kb_id}/documents",
    tags=["documents"],
)

ALLOWED_EXTENSIONS = {
    "pdf": SourceType.pdf,
    "docx": SourceType.docx,
    "txt": SourceType.txt,
}


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


async def _check_doc_limit(kb: KnowledgeBase, current_user: User, session: AsyncSession) -> None:
    """Enforce per-plan document limit."""
    if current_user.plan == Plan.free:
        count_result = await session.execute(
            select(func.count()).select_from(Document).where(Document.knowledge_base_id == kb.id)
        )
        count = count_result.scalar_one()
        if count >= settings.max_docs_free_plan:
            raise HTTPException(
                status_code=403,
                detail=f"Free plan limit of {settings.max_docs_free_plan} documents reached",
            )


@router.post("", response_model=DocumentResponse, status_code=202)
async def upload_document(
    kb_id: str,
    background_tasks: BackgroundTasks,
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_owned_kb(kb_id, current_user, session)
    await _check_doc_limit(kb, current_user, session)

    # Validate extension
    filename = file.filename or "upload"
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_EXTENSIONS:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: .{ext}")

    source_type = ALLOWED_EXTENSIONS[ext]

    max_bytes = settings.max_upload_mb * 1024 * 1024

    # Reject before reading when Content-Length is present (avoids buffering large body)
    if file.size is not None and file.size > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"File exceeds maximum size of {settings.max_upload_mb} MB",
        )

    # Stream-read with early abort to avoid holding the full body in memory
    chunks: list[bytes] = []
    total = 0
    chunk_size = 64 * 1024  # 64 KB
    while True:
        chunk = await file.read(chunk_size)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413,
                detail=f"File exceeds maximum size of {settings.max_upload_mb} MB",
            )
        chunks.append(chunk)
    content = b"".join(chunks)

    # Save to storage
    import uuid
    doc_id = str(uuid.uuid4())
    storage_dir = Path("storage") / kb_id / doc_id
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = str(storage_dir / filename)
    with open(storage_path, "wb") as f:
        f.write(content)

    doc = Document(
        id=doc_id,
        knowledge_base_id=kb.id,
        filename=filename,
        source_type=source_type,
        storage_path=storage_path,
        status=DocStatus.pending,
        size_bytes=len(content),
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    background_tasks.add_task(ingest_document, doc.id)

    return doc


@router.post("/url", response_model=DocumentResponse, status_code=202)
async def ingest_url(
    kb_id: str,
    body: IngestUrlRequest,
    background_tasks: BackgroundTasks,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_owned_kb(kb_id, current_user, session)
    await _check_doc_limit(kb, current_user, session)

    url_str = str(body.url)
    ok, reason = _check_url_safety(url_str)
    if not ok:
        raise HTTPException(status_code=400, detail=reason)

    import uuid
    doc_id = str(uuid.uuid4())

    doc = Document(
        id=doc_id,
        knowledge_base_id=kb.id,
        filename=url_str,
        source_type=SourceType.url,
        source_url=url_str,
        status=DocStatus.pending,
    )
    session.add(doc)
    await session.commit()
    await session.refresh(doc)

    background_tasks.add_task(ingest_document, doc.id)

    return doc


@router.get("", response_model=list[DocumentResponse])
async def list_documents(
    kb_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_owned_kb(kb_id, current_user, session)
    result = await session.execute(
        select(Document).where(Document.knowledge_base_id == kb.id)
    )
    return result.scalars().all()


@router.get("/{doc_id}", response_model=DocumentResponse)
async def get_document(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_owned_kb(kb_id, current_user, session)
    doc = await session.get(Document, doc_id)
    if not doc or doc.knowledge_base_id != kb.id:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@router.delete("/{doc_id}", status_code=204)
async def delete_document(
    kb_id: str,
    doc_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    kb = await _get_owned_kb(kb_id, current_user, session)
    doc = await session.get(Document, doc_id)
    if not doc or doc.knowledge_base_id != kb.id:
        raise HTTPException(status_code=404, detail="Document not found")

    # Remove vectors from Qdrant
    try:
        qdrant = get_qdrant_instance()
        await qdrant.delete_by_document(kb.collection_name, doc.id)
    except Exception:
        pass

    # Remove storage file
    if doc.storage_path and os.path.exists(doc.storage_path):
        try:
            os.remove(doc.storage_path)
        except Exception:
            pass

    # Decrement KB counters
    kb.doc_count = max(0, (kb.doc_count or 1) - 1)
    kb.chunk_count = max(0, (kb.chunk_count or 0) - (doc.chunk_count or 0))
    kb.updated_at = datetime.now(UTC)

    await session.delete(doc)
    await session.commit()
