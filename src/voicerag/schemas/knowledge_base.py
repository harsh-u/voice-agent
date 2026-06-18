from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class KnowledgeBaseCreate(BaseModel):
    name: str
    description: Optional[str] = None
    enable_hybrid: Optional[bool] = True


class KnowledgeBaseUpdate(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None
    enable_hybrid: Optional[bool] = None


class KnowledgeBaseResponse(BaseModel):
    id: str
    user_id: str
    name: str
    description: Optional[str]
    collection_name: str
    embedding_model: Optional[str]
    enable_hybrid: bool
    doc_count: int
    chunk_count: int
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}
