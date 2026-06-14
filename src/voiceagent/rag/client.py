"""Thin async client for the VoiceRAG service.

Used inside the voice pipeline as a function-call handler: when the LLM
invokes `query_knowledge_base`, this module fetches relevant chunks and
returns a concise context string that goes back into the LLM context.
"""
from __future__ import annotations

import httpx
from loguru import logger

from voiceagent.config import settings

_TIMEOUT = 5.0
_MAX_CONTEXT_CHARS = 800  # keep answer speakable


async def query(question: str, api_key: str) -> str:
    """Query the RAG service and return a context string for the LLM.

    Args:
        question: The user's question to look up.
        api_key: Knowledge-base API key (per-agent, from AgentConfig.rag_api_key).

    Returns:
        Retrieved context text, or a short fallback string on error.
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.post(
                f"{settings.rag_base_url.rstrip('/')}/v1/query",
                json={"query": question, "top_k": 3},
                headers={"X-API-Key": api_key},
            )
            resp.raise_for_status()
            data = resp.json()
            context: str = data.get("context") or ""
            if not context:
                return "I don't have specific information about that in my knowledge base."
            # Truncate to stay under LLM token budget for voice
            return context[:_MAX_CONTEXT_CHARS]
    except httpx.TimeoutException:
        logger.warning("[rag] query timed out for question: %s", question[:80])
        return "I'm having trouble accessing my knowledge base right now."
    except Exception as exc:
        logger.warning("[rag] query error: %s", exc)
        return "I don't have specific information about that."
