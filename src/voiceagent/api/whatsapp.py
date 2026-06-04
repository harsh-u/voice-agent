"""WhatsApp config, send, templates, webhook."""
import hashlib
import hmac
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request, Response
from loguru import logger
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from voiceagent.api.auth import get_current_user
from voiceagent.config import settings
from voiceagent.db.models import MessageTemplate, TemplateStatus, User, WhatsAppConfig
from voiceagent.db.session import get_session, AsyncSessionLocal
from voiceagent.whatsapp.encryption import decrypt, encrypt
from voiceagent.whatsapp.meta_api import MetaAPI
from voiceagent.whatsapp.webhook_handler import handle_webhook

router = APIRouter(prefix="/whatsapp", tags=["whatsapp"])
webhook_router = APIRouter(prefix="/webhooks", tags=["webhooks"])


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class WhatsAppConfigResponse(BaseModel):
    id: str
    phone_number_id: str
    waba_id: Optional[str] = None
    webhook_verify_token: Optional[str] = None
    has_access_token: bool = False
    created_at: datetime

    model_config = {"from_attributes": True}


class WhatsAppConfigUpdate(BaseModel):
    phone_number_id: Optional[str] = None
    waba_id: Optional[str] = None
    access_token: Optional[str] = None  # plain text — will be encrypted
    webhook_verify_token: Optional[str] = None
    app_secret: Optional[str] = None


class TemplateCreate(BaseModel):
    name: str
    language: str = "en_US"
    category: str
    components: Optional[list] = None


class TemplateResponse(BaseModel):
    id: str
    name: str
    language: str
    category: str
    components: Optional[list] = None
    status: str
    wa_template_id: Optional[str] = None
    created_at: datetime

    model_config = {"from_attributes": True}


class SendRequest(BaseModel):
    to: str
    type: str = "text"
    content: dict  # {"text": "..."} or {"name": "...", "language": "..."}


# ---------------------------------------------------------------------------
# Config endpoints
# ---------------------------------------------------------------------------

@router.get("/config", response_model=WhatsAppConfigResponse)
async def get_config(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.user_id == current_user.id)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        raise HTTPException(404, "WhatsApp not configured")
    resp = WhatsAppConfigResponse.model_validate(cfg)
    resp.has_access_token = bool(cfg.access_token_enc)
    return resp


@router.put("/config", response_model=WhatsAppConfigResponse)
async def upsert_config(
    payload: WhatsAppConfigUpdate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.user_id == current_user.id)
    )
    cfg = result.scalar_one_or_none()
    if not cfg:
        cfg = WhatsAppConfig(
            user_id=current_user.id,
            phone_number_id=payload.phone_number_id or "",
        )
        session.add(cfg)

    data = payload.model_dump(exclude_unset=True, exclude={"access_token"})
    for k, v in data.items():
        setattr(cfg, k, v)

    if payload.access_token:
        cfg.access_token_enc = encrypt(payload.access_token)

    await session.commit()
    await session.refresh(cfg)
    resp = WhatsAppConfigResponse.model_validate(cfg)
    resp.has_access_token = bool(cfg.access_token_enc)
    return resp


# ---------------------------------------------------------------------------
# Send endpoint
# ---------------------------------------------------------------------------

@router.post("/send")
async def send_message(
    payload: SendRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    cfg_q = await session.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.user_id == current_user.id)
    )
    cfg = cfg_q.scalar_one_or_none()
    if not cfg or not cfg.access_token_enc:
        raise HTTPException(400, "WhatsApp not configured")

    api = MetaAPI(
        phone_number_id=cfg.phone_number_id,
        access_token=decrypt(cfg.access_token_enc),
    )

    try:
        if payload.type == "text":
            result = await api.send_text(payload.to, payload.content.get("text", ""))
        elif payload.type == "template":
            result = await api.send_template(
                payload.to,
                payload.content.get("name", ""),
                payload.content.get("language", "en_US"),
                payload.content.get("components"),
            )
        else:
            result = await api.send_media(
                payload.to,
                media_type=payload.type,
                media_url=payload.content.get("url"),
                media_id=payload.content.get("media_id"),
                caption=payload.content.get("caption"),
            )
    except Exception as e:
        raise HTTPException(502, f"Meta API error: {e}")

    return result


# ---------------------------------------------------------------------------
# Templates
# ---------------------------------------------------------------------------

@router.get("/templates", response_model=list[TemplateResponse])
async def list_templates(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(MessageTemplate)
        .where(MessageTemplate.user_id == current_user.id)
        .order_by(MessageTemplate.created_at.desc())
    )
    return list(result.scalars().all())


@router.post("/templates", response_model=TemplateResponse, status_code=201)
async def create_template(
    payload: TemplateCreate,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    tmpl = MessageTemplate(
        user_id=current_user.id,
        name=payload.name,
        language=payload.language,
        category=payload.category,
        components=payload.components,
        status=TemplateStatus.pending,
    )
    session.add(tmpl)

    # Try to submit to Meta if config exists
    cfg_q = await session.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.user_id == current_user.id)
    )
    cfg = cfg_q.scalar_one_or_none()
    if cfg and cfg.access_token_enc and cfg.waba_id:
        try:
            api = MetaAPI(cfg.phone_number_id, decrypt(cfg.access_token_enc))
            resp = await api.create_template(
                cfg.waba_id, payload.name, payload.language,
                payload.category, payload.components or [],
            )
            tmpl.wa_template_id = resp.get("id")
        except Exception as e:
            logger.error(f"Meta template submission failed: {e}")

    await session.commit()
    await session.refresh(tmpl)
    return tmpl


@router.post("/templates/sync")
async def sync_templates_from_meta(
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Pull all templates from Meta WABA and upsert into local DB with correct status + category."""
    cfg_q = await session.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.user_id == current_user.id)
    )
    cfg = cfg_q.scalar_one_or_none()
    if not cfg or not cfg.access_token_enc or not cfg.waba_id:
        raise HTTPException(400, "WhatsApp config with WABA ID and access token required")

    api = MetaAPI(cfg.phone_number_id, decrypt(cfg.access_token_enc))

    # Fetch templates from Meta Graph API
    import httpx
    graph_url = f"https://graph.facebook.com/v19.0/{cfg.waba_id}/message_templates"
    params = {"limit": 250, "fields": "name,status,category,language,components,id"}
    headers = {"Authorization": f"Bearer {decrypt(cfg.access_token_enc)}"}

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(graph_url, params=params, headers=headers)
            resp.raise_for_status()
            meta_data = resp.json()
    except Exception as e:
        raise HTTPException(502, f"Failed to fetch templates from Meta: {e}")

    meta_templates = meta_data.get("data", [])
    inserted = 0
    updated = 0

    # Meta status → our TemplateStatus mapping
    status_map = {
        "APPROVED": TemplateStatus.approved,
        "ACTIVE": TemplateStatus.approved,
        "PENDING": TemplateStatus.pending,
        "REJECTED": TemplateStatus.rejected,
        "PAUSED": TemplateStatus.pending,
        "DISABLED": TemplateStatus.rejected,
    }

    for mt in meta_templates:
        name = mt.get("name", "")
        language = mt.get("language", "en_US")
        meta_status = mt.get("status", "PENDING").upper()
        category = mt.get("category", "UTILITY")
        wa_id = mt.get("id")
        components = mt.get("components", [])
        new_status = status_map.get(meta_status, TemplateStatus.pending)

        # Find existing by name + language
        existing_q = await session.execute(
            select(MessageTemplate).where(
                MessageTemplate.user_id == current_user.id,
                MessageTemplate.name == name,
                MessageTemplate.language == language,
            )
        )
        existing = existing_q.scalar_one_or_none()

        if existing:
            existing.status = new_status
            existing.category = category
            existing.wa_template_id = wa_id
            existing.components = components
            updated += 1
        else:
            session.add(MessageTemplate(
                user_id=current_user.id,
                name=name,
                language=language,
                category=category,
                components=components,
                status=new_status,
                wa_template_id=wa_id,
            ))
            inserted += 1

    await session.commit()
    logger.info(f"Template sync: {inserted} inserted, {updated} updated from Meta")
    return {"total": len(meta_templates), "inserted": inserted, "updated": updated}


@router.patch("/templates/{template_id}", response_model=TemplateResponse)
async def update_template_status(
    template_id: str,
    payload: dict,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    """Manually update template status/category — useful when Meta API sync fails due to token scope."""
    result = await session.execute(
        select(MessageTemplate).where(
            MessageTemplate.id == template_id, MessageTemplate.user_id == current_user.id
        )
    )
    tmpl = result.scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Template not found")

    status_map = {
        "approved": TemplateStatus.approved, "APPROVED": TemplateStatus.approved,
        "pending": TemplateStatus.pending, "PENDING": TemplateStatus.pending,
        "rejected": TemplateStatus.rejected, "REJECTED": TemplateStatus.rejected,
    }
    if "status" in payload:
        tmpl.status = status_map.get(payload["status"], tmpl.status)
    if "category" in payload:
        tmpl.category = payload["category"]
    if "wa_template_id" in payload:
        tmpl.wa_template_id = payload["wa_template_id"]

    await session.commit()
    await session.refresh(tmpl)
    return tmpl


@router.delete("/templates/{template_id}", status_code=204)
async def delete_template(
    template_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    result = await session.execute(
        select(MessageTemplate).where(
            MessageTemplate.id == template_id, MessageTemplate.user_id == current_user.id
        )
    )
    tmpl = result.scalar_one_or_none()
    if not tmpl:
        raise HTTPException(404, "Template not found")
    await session.delete(tmpl)
    await session.commit()


# ---------------------------------------------------------------------------
# Media proxy
# ---------------------------------------------------------------------------

@router.get("/media/{media_id}")
async def get_media(
    media_id: str,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    cfg_q = await session.execute(
        select(WhatsAppConfig).where(WhatsAppConfig.user_id == current_user.id)
    )
    cfg = cfg_q.scalar_one_or_none()
    if not cfg or not cfg.access_token_enc:
        raise HTTPException(400, "WhatsApp not configured")
    api = MetaAPI(cfg.phone_number_id, decrypt(cfg.access_token_enc))
    try:
        url = await api.get_media_url(media_id)
        data = await api.download_media(url)
    except Exception as e:
        raise HTTPException(502, f"Media download failed: {e}")
    return Response(content=data, media_type="application/octet-stream")


# ---------------------------------------------------------------------------
# Webhooks
# ---------------------------------------------------------------------------

@webhook_router.get("/whatsapp")
async def whatsapp_webhook_verify(
    request: Request,
    hub_mode: str = Query(None, alias="hub.mode"),
    hub_challenge: str = Query(None, alias="hub.challenge"),
    hub_verify_token: str = Query(None, alias="hub.verify_token"),
):
    """Meta webhook verification challenge.

    Meta calls this URL when you register a webhook in the Meta Developer
    Console.  The verify_token must match the one saved in WhatsAppConfig.
    """
    if hub_mode != "subscribe" or not hub_verify_token:
        raise HTTPException(403, "Forbidden")

    # Validate token against every WhatsAppConfig in the DB
    from voiceagent.db.session import AsyncSessionLocal
    from voiceagent.db.models import WhatsAppConfig
    from sqlalchemy import select

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(WhatsAppConfig).where(
                WhatsAppConfig.webhook_verify_token == hub_verify_token
            )
        )
        cfg = result.scalar_one_or_none()

    if not cfg:
        logger.warning(f"Webhook verify failed — unknown token: {hub_verify_token!r}")
        raise HTTPException(403, "Invalid verify token")

    logger.info(f"Webhook verified for phone_number_id={cfg.phone_number_id}")
    return Response(content=hub_challenge or "", media_type="text/plain")


@webhook_router.post("/whatsapp")
async def whatsapp_webhook(request: Request):
    """Receive inbound WhatsApp messages from Meta."""
    body = await request.body()

    # HMAC verification if app_secret is configured
    app_secret = settings.meta_app_secret
    if app_secret:
        sig = request.headers.get("x-hub-signature-256", "")
        expected = "sha256=" + hmac.new(
            app_secret.encode(), body, hashlib.sha256
        ).hexdigest()
        if not hmac.compare_digest(sig, expected):
            logger.warning("WhatsApp webhook HMAC verification failed")
            raise HTTPException(401, "Invalid signature")

    import json
    try:
        payload = json.loads(body)
    except Exception:
        raise HTTPException(400, "Invalid JSON")

    # Process async — don't block Meta's 20s window
    async with AsyncSessionLocal() as session:
        await handle_webhook(payload, session)

    return {"status": "ok"}
