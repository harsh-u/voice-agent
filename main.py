import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from voiceagent.api import agents, calls, webhooks
from voiceagent.db.session import create_tables


@asynccontextmanager
async def lifespan(app: FastAPI):
    await create_tables()
    yield


app = FastAPI(
    title="Voice AI Platform",
    description="Low-latency voice AI — ~$0.04/min, <500ms latency",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "version": "1.0.0"}


app.include_router(agents.router)
app.include_router(calls.router)
app.include_router(webhooks.router)

recordings_dir = Path("recordings")
recordings_dir.mkdir(exist_ok=True)
app.mount("/recordings", StaticFiles(directory="recordings"), name="recordings")

web_dir = Path("web")
if web_dir.exists():
    app.mount("/", StaticFiles(directory="web", html=True), name="web")
