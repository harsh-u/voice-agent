"""Contacts and Tags CRUD."""
from datetime import datetime
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, status
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from voiceagent.api.auth import get_current_user
from voiceagent.db.models import Contact, ContactTag, Tag, User
from voiceagent.db.session import get_session

router = APIRouter(prefix="/contacts", tags=["contacts"])
tags_router = APIRouter(prefix="/tags", tags=["tags"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class TagResponse(BaseModel):
    id: str
    name: str
    color: Optional[str] = None

    model_config = {"from_attributes": True}


class ContactCreate(BaseModel):
    phone: str
    name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    custom_fields: Optional[dict] = None


class ContactUpdate(BaseModel):
    phone: Optional[str] = None
    name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    custom_fields: Optional[dict] = None


class ContactResponse(BaseModel):
    id: str
    phone: str
    name: Optional[str] = None
    email: Optional[str] = None
    company: Optional[str] = None
    custom_fields: Optional[dict] = None
    created_at: datetime
    tags: list[TagResponse] = []

    model_config = {"from_attributes": True}


class TagCreate(BaseModel):
    name: str
    color: Optional[str] = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

async def _get_contact(contact_id: str, user_id: str, session: AsyncSession) -> Contact:
    result = await session.execute(
        select(Contact)
        .where(Contact.id == contact_id, Contact.user_id == user_id)
        .options(selectinload(Contact.tags))
    )
    c = result.scalar_one_or_none()
    if not c:
        raise HTTPException(404, "Contact not found")
    return c


async def _get_tag(tag_id: str, user_id: str, session: AsyncSession) -> Tag:
    result = await session.execute(
        select(Tag).where(Tag.id == tag_id, Tag.user_id == user_id)
    )
    t = result.scalar_one_or_none()
    if not t:
        raise HTTPException(404, "Tag not found")
    return t


# ---------------------------------------------------------------------------
# Contact endpoints
# ---------------------------------------------------------------------------

@router.get("", response_model=list[ContactResponse])
async def list_contacts(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    search: Optional[str] = Query(None),
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Contact)
        .where(Contact.user_id == current_user.id)
        .options(selectinload(Contact.tags))
    )
    if search:
        like = f"%{search}%"
        stmt = stmt.where(or_(Contact.name.ilike(like), Contact.phone.ilike(like)))
    stmt = stmt.order_by(Contact.created_at.desc()).offset(skip).limit(limit)
    result = await session.execute(stmt)
    return list(result.scalars().all())


@router.post("", response_model=ContactResponse, status_code=201)
async def create_contact(
    payload: ContactCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    existing = await session.execute(
        select(Contact).where(Contact.user_id == current_user.id, Contact.phone == payload.phone)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Contact with this phone already exists")

    contact = Contact(
        user_id=current_user.id,
        phone=payload.phone,
        name=payload.name,
        email=payload.email,
        company=payload.company,
        custom_fields=payload.custom_fields,
    )
    session.add(contact)
    await session.commit()
    await session.refresh(contact, attribute_names=["tags"])
    return contact


@router.get("/{contact_id}", response_model=ContactResponse)
async def get_contact(
    contact_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    return await _get_contact(contact_id, current_user.id, session)


@router.patch("/{contact_id}", response_model=ContactResponse)
async def update_contact(
    contact_id: str,
    payload: ContactUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    contact = await _get_contact(contact_id, current_user.id, session)
    for k, v in payload.model_dump(exclude_unset=True).items():
        setattr(contact, k, v)
    await session.commit()
    await session.refresh(contact, attribute_names=["tags"])
    return contact


@router.delete("/{contact_id}", status_code=204)
async def delete_contact(
    contact_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    contact = await _get_contact(contact_id, current_user.id, session)
    await session.delete(contact)
    await session.commit()


@router.post("/{contact_id}/tags/{tag_id}", response_model=ContactResponse)
async def add_tag(
    contact_id: str,
    tag_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    contact = await _get_contact(contact_id, current_user.id, session)
    tag = await _get_tag(tag_id, current_user.id, session)
    existing = await session.execute(
        select(ContactTag).where(ContactTag.contact_id == contact.id, ContactTag.tag_id == tag.id)
    )
    if not existing.scalar_one_or_none():
        session.add(ContactTag(contact_id=contact.id, tag_id=tag.id))
        await session.commit()
    await session.refresh(contact, attribute_names=["tags"])
    return contact


@router.delete("/{contact_id}/tags/{tag_id}", response_model=ContactResponse)
async def remove_tag(
    contact_id: str,
    tag_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    contact = await _get_contact(contact_id, current_user.id, session)
    result = await session.execute(
        select(ContactTag).where(ContactTag.contact_id == contact_id, ContactTag.tag_id == tag_id)
    )
    link = result.scalar_one_or_none()
    if link:
        await session.delete(link)
        await session.commit()
    await session.refresh(contact, attribute_names=["tags"])
    return contact


# ---------------------------------------------------------------------------
# Tag endpoints
# ---------------------------------------------------------------------------

@tags_router.get("", response_model=list[TagResponse])
async def list_tags(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(Tag).where(Tag.user_id == current_user.id).order_by(Tag.name)
    )
    return list(result.scalars().all())


@tags_router.post("", response_model=TagResponse, status_code=201)
async def create_tag(
    payload: TagCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    existing = await session.execute(
        select(Tag).where(Tag.user_id == current_user.id, Tag.name == payload.name)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(409, "Tag already exists")
    tag = Tag(user_id=current_user.id, name=payload.name, color=payload.color)
    session.add(tag)
    await session.commit()
    await session.refresh(tag)
    return tag


@tags_router.delete("/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    tag = await _get_tag(tag_id, current_user.id, session)
    await session.delete(tag)
    await session.commit()
