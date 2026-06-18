"""JWT authentication helpers and get_current_user dependency."""
from datetime import datetime, timedelta, UTC
from typing import Optional

from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer
from jose import JWTError, jwt
import bcrypt as _bcrypt
from sqlalchemy.ext.asyncio import AsyncSession

from voxscope.config import settings
from voxscope.db.models import User
from voxscope.db.session import get_session

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/v1/auth/token")


# ---------------------------------------------------------------------------
# Password helpers
# ---------------------------------------------------------------------------

def _hash(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def _verify(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


# ---------------------------------------------------------------------------
# JWT helpers
# ---------------------------------------------------------------------------

def _make_token(sub: str, kind: str, expire_delta: timedelta) -> str:
    payload = {
        "sub": sub,
        "kind": kind,
        "exp": datetime.now(UTC) + expire_delta,
    }
    return jwt.encode(payload, settings.jwt_secret, algorithm=settings.jwt_algorithm)


def make_access_token(user_id: str) -> str:
    return _make_token(
        user_id, "access",
        timedelta(minutes=settings.jwt_access_expire_minutes),
    )


def make_refresh_token(user_id: str) -> str:
    return _make_token(
        user_id, "refresh",
        timedelta(days=settings.jwt_refresh_expire_days),
    )


# ---------------------------------------------------------------------------
# Dependency
# ---------------------------------------------------------------------------

async def get_current_user(
    token: str = Depends(oauth2_scheme),
    session: AsyncSession = Depends(get_session),
) -> User:
    """FastAPI dependency — resolves Bearer token to a User row."""
    exc = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Invalid or expired token",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, settings.jwt_secret, algorithms=[settings.jwt_algorithm])
        user_id: Optional[str] = payload.get("sub")
        kind: Optional[str] = payload.get("kind")
        if not user_id or kind != "access":
            raise exc
    except JWTError:
        raise exc

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise exc
    return user
