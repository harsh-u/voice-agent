"""Recursive sentence/paragraph chunker for voice RAG."""
import re
from dataclasses import dataclass
from typing import Optional

from voicerag.config import settings


@dataclass
class Chunk:
    text: str
    index: int           # position within document
    token_estimate: int


def _estimate_tokens(text: str) -> int:
    """Estimate token count as len(text) // 4 (no tokenizer dependency)."""
    return max(1, len(text) // 4)


def _normalize(text: str) -> str:
    """Collapse excessive blank lines and normalize whitespace."""
    # Normalize line endings
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse 3+ blank lines to 2
    text = re.sub(r"\n{3,}", "\n\n", text)
    # Collapse runs of spaces/tabs on a line
    text = re.sub(r"[ \t]+", " ", text)
    return text.strip()


def _split_sentences(text: str) -> list[str]:
    """
    Split on sentence boundaries: period/!/?  followed by whitespace,
    and on paragraph breaks (\n\n). Preserves non-empty segments.
    """
    # Split on paragraph boundaries first
    paragraphs = re.split(r"\n\n+", text)
    sentences: list[str] = []
    for para in paragraphs:
        para = para.strip()
        if not para:
            continue
        # Split para into sentences
        # Match end-of-sentence punctuation followed by space or end
        parts = re.split(r"(?<=[.!?])\s+", para)
        for part in parts:
            part = part.strip()
            if part:
                sentences.append(part)
    return sentences


def chunk_text(
    text: str,
    target_tokens: int = None,
    overlap_tokens: int = None,
) -> list[Chunk]:
    """
    Split text into overlapping chunks targeting ~target_tokens each.

    Strategy:
      1. Normalize whitespace.
      2. Split into sentences.
      3. Pack sentences greedily until target is reached.
      4. Apply overlap by carrying tail sentences of previous chunk.
      5. Drop chunks with < 10 non-whitespace chars.
    """
    if target_tokens is None:
        target_tokens = settings.chunk_target_tokens
    if overlap_tokens is None:
        overlap_tokens = settings.chunk_overlap_tokens

    text = _normalize(text)
    if not text:
        return []

    sentences = _split_sentences(text)
    if not sentences:
        return []

    chunks: list[Chunk] = []
    current: list[str] = []
    current_tokens = 0
    overlap_carry: list[str] = []  # sentences to prepend for overlap

    def flush(current_sentences: list[str], idx: int) -> Optional[Chunk]:
        chunk_text = " ".join(current_sentences).strip()
        if len(chunk_text.replace(" ", "")) < 10:
            return None
        tok = _estimate_tokens(chunk_text)
        return Chunk(text=chunk_text, index=idx, token_estimate=tok)

    chunk_idx = 0

    i = 0
    # Seed with overlap from previous chunk (empty at start)
    current = list(overlap_carry)
    current_tokens = sum(_estimate_tokens(s) for s in current)

    while i < len(sentences):
        sent = sentences[i]
        sent_tok = _estimate_tokens(sent)

        if current_tokens + sent_tok <= target_tokens or not current:
            current.append(sent)
            current_tokens += sent_tok
            i += 1
        else:
            # Flush current chunk
            c = flush(current, chunk_idx)
            if c:
                chunks.append(c)
                chunk_idx += 1

            # Build overlap: take tail sentences whose total tokens ~ overlap_tokens
            overlap_carry = []
            carry_tok = 0
            for s in reversed(current):
                st = _estimate_tokens(s)
                if carry_tok + st <= overlap_tokens:
                    overlap_carry.insert(0, s)
                    carry_tok += st
                else:
                    break

            current = list(overlap_carry)
            current_tokens = carry_tok
            # Don't advance i — reprocess this sentence in new chunk

    # Flush remaining
    if current:
        c = flush(current, chunk_idx)
        if c:
            chunks.append(c)

    return chunks
