"""Unified platform entrypoint.

ONE FastAPI app serving three merged modules behind one login:
  * voiceagent  — voice calls, CRM, agents, auth (the host product)
  * voicerag    — RAG knowledge bases, mounted under /rag
  * voxscope    — observability (traces/metrics), mounted under /observability

See MERGE_PLAN.md for the architecture. RAG/obs tables live in dedicated Postgres
schemas (rag/obs) in the same database; the voice-agent JWT is bridged into the
mounted routers so there is a single account and a single login.
"""
import asyncio
import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from sqlalchemy import text

from voiceagent.api import agents, calls, webhooks
from voiceagent.api import auth, contacts, conversations, messages, whatsapp, pipelines, broadcasts, dashboard, flows, automations, voice_settings
from voiceagent.api import knowledge
from voiceagent.integrations import install_auth_bridge, provision_workspace

# Merged module routers
from voicerag.api import knowledge_bases as rag_kbs, api_keys as rag_keys, documents as rag_docs, query as rag_query
from voxscope.api import projects as obs_projects, ingest as obs_ingest, traces as obs_traces, metrics as obs_metrics

logger = logging.getLogger(__name__)

RAG_PREFIX = "/rag"
OBS_PREFIX = "/observability"


async def create_all_tables() -> None:
    """Create the rag/obs schemas, then create tables for all three modules."""
    from voiceagent.db.session import engine as va_engine, create_tables as va_create_tables
    async with va_engine.begin() as conn:
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS rag"))
        await conn.execute(text("CREATE SCHEMA IF NOT EXISTS obs"))
    await va_create_tables()

    from voicerag.db.session import create_tables as rag_create_tables
    from voxscope.db.session import create_tables as obs_create_tables
    await rag_create_tables()
    await obs_create_tables()


@asynccontextmanager
async def lifespan(app: FastAPI):
    # 1. Schemas + tables for all three modules
    await create_all_tables()

    # 2. RAG engine runtime: redis cache, qdrant vector store, embedder
    Path("storage").mkdir(exist_ok=True)
    from voicerag.core.redis_client import init_redis, close_redis
    from voicerag.vector.qdrant_store import QdrantStore, set_qdrant_instance
    from voicerag.embedding.embedder import Embedder, set_embedder_instance

    app.state.redis = await init_redis()

    qdrant = QdrantStore()
    await qdrant.init()
    try:
        await qdrant.ping()
    except Exception as exc:
        logger.warning("[startup] Qdrant ping failed (RAG retrieval will fail until reachable): %s", exc)
    set_qdrant_instance(qdrant)
    app.state.qdrant = qdrant

    embedder = Embedder()
    set_embedder_instance(embedder)
    app.state.embedder = embedder

    # 3. Observability engine runtime: in-process ingest queue + background tasks
    from voxscope.runtime import drain_ingest_queue, rollup_task, retention_task
    queue: asyncio.Queue = asyncio.Queue(maxsize=50_000)
    app.state.ingest_queue = queue
    obs_tasks = [
        asyncio.create_task(drain_ingest_queue(queue)),
        asyncio.create_task(rollup_task()),
        asyncio.create_task(retention_task()),
    ]

    # 4. Provision the shared workspace (rag user, obs user/project/ingest key)
    await provision_workspace()

    logger.info("[startup] unified platform ready (voiceagent + /rag + /observability)")
    yield

    # Shutdown — drain obs queue, cancel tasks, close RAG infra
    await queue.put(None)
    try:
        await asyncio.wait_for(queue.join(), timeout=10.0)
    except asyncio.TimeoutError:
        logger.warning("[shutdown] ingest queue did not drain in 10s")
    for t in obs_tasks:
        t.cancel()
    for t in obs_tasks:
        try:
            await t
        except asyncio.CancelledError:
            pass
    await qdrant.close()
    await close_redis()


app = FastAPI(
    title="Convoxio — Voice AI Platform",
    description="Unified voice agents + RAG knowledge bases + observability, in one product.",
    version="2.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:3000", "http://127.0.0.1:3000", "*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", tags=["system"])
async def health():
    return {"status": "ok", "version": "2.0.0", "modules": ["voiceagent", "rag", "observability"]}


# ── Host product: voice + CRM + auth ─────────────────────────────────────────
app.include_router(agents.router)
app.include_router(calls.router)
app.include_router(webhooks.router)
app.include_router(knowledge.router)
app.include_router(auth.router)
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

# ── Merged RAG engine (knowledge bases as an agent feature) ──────────────────
app.include_router(rag_kbs.router, prefix=RAG_PREFIX)
app.include_router(rag_keys.router, prefix=RAG_PREFIX)
app.include_router(rag_docs.router, prefix=RAG_PREFIX)
app.include_router(rag_query.router, prefix=RAG_PREFIX)

# ── Merged observability engine (in-app traces/metrics views) ────────────────
app.include_router(obs_projects.router, prefix=OBS_PREFIX)
app.include_router(obs_ingest.router, prefix=OBS_PREFIX)
app.include_router(obs_traces.router, prefix=OBS_PREFIX)
app.include_router(obs_metrics.router, prefix=OBS_PREFIX)

# Bridge the single voice-agent JWT into the /rag and /observability routers
install_auth_bridge(app)

# ── Static mounts ────────────────────────────────────────────────────────────
recordings_dir = Path("recordings")
recordings_dir.mkdir(exist_ok=True)
app.mount("/recordings", StaticFiles(directory="recordings"), name="recordings")

media_dir = Path("media")
media_dir.mkdir(exist_ok=True)
app.mount("/media", StaticFiles(directory="media"), name="media")

web_dir = Path("web")
if web_dir.exists():
    app.mount("/", StaticFiles(directory="web", html=True), name="web")
