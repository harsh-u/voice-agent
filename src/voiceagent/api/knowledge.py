"""Knowledge base proxy — forwards document management requests to VoiceRAG.

Each agent with rag_kb_id + rag_api_key set can manage its knowledge base
through these endpoints without the frontend knowing the RAG service URL.
"""
from __future__ import annotations

from typing import Any

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from voiceagent.config import settings
from voiceagent.db.models import AgentConfig
from voiceagent.db.session import get_session

router = APIRouter(prefix="/agents/{agent_id}/knowledge", tags=["knowledge"])

_TIMEOUT = 30.0


async def _get_agent_with_kb(
    agent_id: str,
    session: AsyncSession,
) -> AgentConfig:
    agent = await session.get(AgentConfig, agent_id)
    if not agent:
        raise HTTPException(status_code=404, detail="Agent not found")
    if not agent.rag_kb_id or not agent.rag_api_key:
        raise HTTPException(
            status_code=422,
            detail="Agent does not have a knowledge base configured. Set rag_kb_id and rag_api_key.",
        )
    return agent


def _rag_headers(api_key: str) -> dict[str, str]:
    return {"X-API-Key": api_key}


def _kb_url(kb_id: str, path: str = "") -> str:
    base = settings.rag_base_url.rstrip("/")
    return f"{base}/knowledge-bases/{kb_id}/documents{path}"


@router.get("/documents")
async def list_documents(
    agent_id: str,
    session: AsyncSession = Depends(get_session),
) -> Any:
    """List all documents in the agent's knowledge base."""
    agent = await _get_agent_with_kb(agent_id, session)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.get(
            _kb_url(agent.rag_kb_id),
            headers=_rag_headers(agent.rag_api_key),
        )
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Knowledge base not found in RAG service")
    resp.raise_for_status()
    return resp.json()


@router.post("/documents", status_code=202)
async def upload_document(
    agent_id: str,
    file: UploadFile = File(...),
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Upload a document (PDF, DOCX, TXT) to the agent's knowledge base."""
    agent = await _get_agent_with_kb(agent_id, session)
    content = await file.read()
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _kb_url(agent.rag_kb_id),
            headers=_rag_headers(agent.rag_api_key),
            files={"file": (file.filename, content, file.content_type or "application/octet-stream")},
        )
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Knowledge base not found in RAG service")
    resp.raise_for_status()
    return resp.json()


class UrlIngestRequest(BaseModel):
    url: str
    title: str | None = None


@router.post("/documents/url", status_code=202)
async def ingest_url(
    agent_id: str,
    body: UrlIngestRequest,
    session: AsyncSession = Depends(get_session),
) -> Any:
    """Ingest a public URL into the agent's knowledge base."""
    agent = await _get_agent_with_kb(agent_id, session)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.post(
            _kb_url(agent.rag_kb_id, "/url"),
            headers=_rag_headers(agent.rag_api_key),
            json={"url": body.url, "title": body.title},
        )
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Knowledge base not found in RAG service")
    resp.raise_for_status()
    return resp.json()


@router.delete("/documents/{doc_id}", status_code=204)
async def delete_document(
    agent_id: str,
    doc_id: str,
    session: AsyncSession = Depends(get_session),
) -> None:
    """Remove a document from the agent's knowledge base."""
    agent = await _get_agent_with_kb(agent_id, session)
    async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
        resp = await client.delete(
            _kb_url(agent.rag_kb_id, f"/{doc_id}"),
            headers=_rag_headers(agent.rag_api_key),
        )
    if resp.status_code == 404:
        raise HTTPException(status_code=404, detail="Document not found")
    if resp.status_code != 204:
        resp.raise_for_status()
