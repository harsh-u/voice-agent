from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from voiceagent.api import agents, calls, webhooks
from voiceagent.api import auth, contacts, conversations, messages, whatsapp, pipelines, broadcasts, dashboard, flows, automations, voice_settings
from voiceagent.api import knowledge
from voiceagent.db.session import create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    yield


app = FastAPI(
    title="WhatsApp + Voice AI CRM",
    description="Multi-channel CRM — WhatsApp conversations and AI voice calls unified",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=False,   # credentials=False allows wildcard origins
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "version": "1.0.0"}


# Voice module
app.include_router(agents.router)
app.include_router(calls.router)
app.include_router(webhooks.router)
app.include_router(knowledge.router)

# Auth
app.include_router(auth.router)

# CRM module
app.include_router(contacts.router)
app.include_router(contacts.tags_router)
app.include_router(conversations.router)
app.include_router(messages.router)
app.include_router(whatsapp.router)
app.include_router(whatsapp.webhook_router)
app.include_router(pipelines.router)
app.include_router(pipelines.deals_router)
app.include_router(broadcasts.router)
app.include_router(flows.router)
app.include_router(automations.router)
app.include_router(dashboard.router)
app.include_router(voice_settings.router)

# Static file mounts
recordings_dir = Path("recordings")
recordings_dir.mkdir(exist_ok=True)
app.mount("/recordings", StaticFiles(directory="recordings"), name="recordings")

media_dir = Path("media")
media_dir.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")

web_dir = Path("web")
if web_dir.exists():
    app.mount("/", StaticFiles(directory="web", html=True), name="web")
