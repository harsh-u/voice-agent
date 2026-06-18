from datetime import datetime
from typing import Optional

from pydantic import BaseModel, HttpUrl


class DocumentResponse(BaseModel):
    id: str
    knowledge_base_id: str
    filename: Optional[str]
    source_type: str
    source_url: Optional[str]
    status: str
    error: Optional[str]
    chunk_count: int
    size_bytes: Optional[int]
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class IngestUrlRequest(BaseModel):
    url: HttpUrl
