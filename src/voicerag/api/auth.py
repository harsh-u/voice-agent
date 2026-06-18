"""Auth endpoints: register, token, login, refresh, me."""
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voicerag.core.security import (
    _hash, _verify, make_access_token, make_refresh_token, get_current_user,
)
from voicerag.db.models import User, Plan
from voicerag.db.session import get_session
from voicerag.schemas.auth import (
    RegisterRequest, LoginRequest, TokenResponse, RefreshRequest, UserResponse,
)

from jose import JWTError, jwt
from voicerag.config import settings

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post("/register", response_model=TokenResponse, status_code=201)
async def register(
    body: RegisterRequest,
    session: AsyncSession = Depends(get_session),
):
    existing = await session.execute(select(User).where(User.email == body.email))
    if existing.scalar_one_or_none():
        raise HTTPException(status_code=409, detail="Email already registered")

    user = User(
        email=body.email,
        hashed_password=_hash(body.password),
        full_name=body.full_name,
        plan=Plan.free,
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
    """OAuth2 password flow (used by Swagger UI)."""
    return await _do_login(form.username, form.password, session)


@router.post("/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    session: AsyncSession = Depends(get_session),
):
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
async def refresh(
    body: RefreshRequest,
    session: AsyncSession = Depends(get_session),
):
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
