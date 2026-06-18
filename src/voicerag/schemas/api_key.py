from datetime import datetime
from typing import Optional

from pydantic import BaseModel


class ApiKeyCreate(BaseModel):
    name: Optional[str] = None


class ApiKeyCreatedResponse(BaseModel):
    """Returned only on creation — includes the full key (shown once)."""
    id: str
    name: Optional[str]
    key: str         # full plaintext key — shown ONCE
    key_prefix: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ApiKeyResponse(BaseModel):
    """Used for list — never exposes full key."""
    id: str
    name: Optional[str]
    key_prefix: str
    last_used_at: Optional[datetime]
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}
