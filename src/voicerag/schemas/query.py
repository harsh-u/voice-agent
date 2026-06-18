from typing import Optional

from pydantic import BaseModel


class QueryRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    hybrid: Optional[bool] = None


class RetrievedChunkSchema(BaseModel):
    text: str
    score: float
    document_id: str
    chunk_index: int
    filename: str


class QueryResponse(BaseModel):
    context: str
    chunks: list[RetrievedChunkSchema]
    cache_hit: bool
    latency_ms: float
    top_score: Optional[float]


class SearchResponse(BaseModel):
    chunks: list[RetrievedChunkSchema]
    cache_hit: bool
    latency_ms: float
    top_score: Optional[float]


class AnswerRequest(BaseModel):
    query: str
    top_k: Optional[int] = None
    system_prompt: Optional[str] = None


class AnswerResponse(BaseModel):
    answer: str
    chunks: list[RetrievedChunkSchema]
    latency_ms: float
