"""JWT authentication — register, login, refresh, me."""
from datetime import datetime, timedelta, UTC
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from jose import JWTError, jwt
import bcrypt as _bcrypt
from pydantic import BaseModel, EmailStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voiceagent.config import settings
from voiceagent.db.models import User, UserRole
from voiceagent.db.session import get_session

router = APIRouter(prefix="/auth", tags=["auth"])

oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/token")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _hash(plain: str) -> str:
    return _bcrypt.hashpw(plain.encode(), _bcrypt.gensalt()).decode()


def _verify(plain: str, hashed: str) -> bool:
    try:
        return _bcrypt.checkpw(plain.encode(), hashed.encode())
    except Exception:
        return False


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
        user_id: str = payload.get("sub")
        kind: str = payload.get("kind")
        if not user_id or kind != "access":
            raise exc
    except JWTError:
        raise exc

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise exc
    return user


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    full_name: Optional[str] = None


class TokenResponse(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "bearer"


class RefreshRequest(BaseModel):
    refresh_token: str


class UserResponse(BaseModel):
    id: str
    email: str
    full_name: Optional[str]
    avatar_url: Optional[str]
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(body: RegisterRequest, session: AsyncSession = Depends(get_session)):
    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email,
        hashed_password=_hash(body.password),
        full_name=body.full_name,
        role=UserRole.admin,
    )
    session.add(user)
    await session.commit()
    await session.refresh(user)

    return TokenResponse(
        access_token=make_access_token(user.id),
        refresh_token=make_refresh_token(user.id),
    )


@router.post("/token", response_model=TokenResponse)
async def login_form(
    form: OAuth2PasswordRequestForm = Depends(),
    session: AsyncSession = Depends(get_session),
):
    """OAuth2 password flow (used by Swagger UI / docs)."""
    return await _do_login(form.username, form.password, session)


@router.post("/login", response_model=TokenResponse)
async def login(body: RegisterRequest, session: AsyncSession = Depends(get_session)):
    return await _do_login(body.email, body.password, session)


async def _do_login(email: str, password: str, session: AsyncSession) -> TokenResponse:
    result = await session.execute(select(User).where(User.email == email))
    user = result.scalar_one_or_none()
    if not user or not _verify(password, user.hashed_password):
        raise HTTPException(status_code=401, detail="Invalid credentials")
    if not user.is_active:
        raise HTTPException(status_code=403, detail="Account disabled")
    return TokenResponse(
        access_token=make_access_token(user.id),
        refresh_token=make_refresh_token(user.id),
    )


@router.post("/refresh", response_model=TokenResponse)
async def refresh(body: RefreshRequest, session: AsyncSession = Depends(get_session)):
    exc = HTTPException(status_code=401, detail="Invalid refresh token")
    try:
        payload = jwt.decode(
            body.refresh_token, settings.jwt_secret,
            algorithms=[settings.jwt_algorithm],
        )
        user_id = payload.get("sub")
        kind = payload.get("kind")
        if not user_id or kind != "refresh":
            raise exc
    except JWTError:
        raise exc

    user = await session.get(User, user_id)
    if not user or not user.is_active:
        raise exc

    return TokenResponse(
        access_token=make_access_token(user.id),
        refresh_token=make_refresh_token(user.id),
    )


@router.get("/me", response_model=UserResponse)
async def me(current_user: User = Depends(get_current_user)):
    return current_user
