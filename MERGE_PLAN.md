# Unified Platform Merge Plan

> Merging **VoiceRAG** (rag) and **VoxScope** (observability) into the **voice-agent**
> so they become a single product: one backend, one frontend, one login, one deploy.
> RAG is exposed as an *agent feature* (knowledge bases the voice agent can query);
> Observability is exposed as in-app *data views* (traces, latency, costs).

Decisions (locked):
- **Topology:** Modular monolith — one FastAPI app, one Next.js frontend, one Postgres.
- **Auth:** Single unified identity (voice-agent `User` + JWT). RAG knowledge bases and
  VoxScope projects auto-provisioned per user. Internal API keys (`vrag_`, `vsk_`) managed
  server-side and hidden from the user.

---

## Target architecture

```
voice-agent/                      ONE deployable
  main.py                         unified FastAPI app (:8000)
  worker.py                       LiveKit voice worker (separate process)
  src/
    voiceagent/                   existing: voice, CRM, auth, pipeline
      rag/client.py               -> calls unified backend  /rag/v1/query        (HTTP, cross-process)
      observability/client.py     -> calls unified backend  /observability/v1/ingest/batch
      integrations.py             NEW: auto-provision shadow rag user + obs project, mint internal keys
    voicerag/                     merged RAG engine (package name preserved)
      db/models.py                Base.metadata schema = "rag"
      api/*                       mounted under /rag
    voxscope/                     merged observability engine (package name preserved)
      db/models.py                Base.metadata schema = "obs"
      api/*                       mounted under /observability
  frontend/                       Next.js — adds /knowledge and /observability sections
Postgres (one DB):  public.*  (voiceagent)   |  rag.*  (voicerag)   |  obs.*  (voxscope)
Infra:  Qdrant (RAG vectors) + Redis (RAG cache) + Postgres
```

### Why HTTP between worker and engines
The Pipecat pipeline runs inside `worker.py`, a **separate process** from the FastAPI
server. So `rag/client.py` and `observability/client.py` keep talking HTTP — but to the
*same unified backend* (localhost, `/rag` and `/observability` prefixes). The frontend-facing
RAG/obs routes are mounted **in-process** in the one FastAPI app. Net result: one product,
correct process boundaries, negligible (~1ms) localhost overhead vs. ~30ms retrieval.

---

## Database namespacing (one DB, no table collisions)
Each engine keeps its own `DeclarativeBase`; we move RAG/obs tables into Postgres **schemas**:
- voiceagent  -> `public`  (unchanged)
- voicerag    -> `rag`     (`Base.metadata = MetaData(schema="rag")`)
- voxscope    -> `obs`     (`Base.metadata = MetaData(schema="obs")`)

Schemas created at startup (`CREATE SCHEMA IF NOT EXISTS ...`). This eliminates the
`users` / `api_keys` name collisions without rewriting models. The three engines all read
the same `DATABASE_URL` (one `.env`).

`rag.users` and `obs.users` become **shadow** tables, auto-provisioned to mirror the
canonical `public.users` row (same UUID, same email) so existing FKs and `get_current_user`
lookups keep working unchanged.

---

## Unified auth bridge
1. Single `JWT_SECRET` + algorithm shared across all three modules (one `.env`).
2. On voice-agent **signup** (and lazily on login if missing), `integrations.py`:
   - inserts a `rag.users` row with the **same id/email** as the voiceagent user,
   - inserts an `obs.users` row + a default `obs.projects` row,
   - mints a default RAG knowledge base + `vrag_` query key and a `vsk_` ingest key,
     stored server-side (on `User` or a `user_integrations` row), never shown.
3. Because shadow users share the canonical UUID and the JWT secret is shared, a
   voice-agent JWT (`sub = user.id`) validates *and* resolves in the `/rag` and
   `/observability` routers with no per-module login.

---

## Phases
- **P1 Backend structure** — copy `voicerag/` + `voxscope/` into `src/`; schema-namespace
  their Bases; merge `pyproject.toml` deps; create schemas on startup.
- **P2 Unified app** — `main.py` mounts RAG routers under `/rag`, obs routers under
  `/observability`; merge the three lifespans (RAG: redis+qdrant+embedder; obs: ingest
  queue + rollup + retention tasks); `create_tables()` for all three.
- **P3 Auth bridge** — shared JWT; `integrations.py` provisioning; make `/rag` + `/obs`
  security deps accept the voiceagent JWT; hide internal keys.
- **P4 Pipeline rewire** — point `rag/client.py` at `/rag/v1/query`, `observability/client.py`
  at `/observability/v1/ingest/batch`; auto-provision KB+keys on agent create so RAG works
  without manual key entry.
- **P5 Frontend** — add `/knowledge` (KB + document management) and `/observability`
  (traces list, trace detail + 3D waterfall, latency metrics) sections to the Next.js app;
  add nav; route through the existing `/api/[...path]` proxy.
- **P6 Infra + docs** — unified `docker-compose.yml` (postgres, qdrant, redis, api, worker,
  frontend), unified `.env(.example)`, update PRODUCT.md / READMEs.
- **P7 Verify** — install deps, boot backend, smoke-test health + `/rag` + `/observability`.

## Non-goals (this pass)
- Rewriting RAG/obs into native voiceagent modules (that's the "full monolith" option).
- In-process retrieval inside the worker (kept HTTP by design).
- Data migration from the old separate `voicerag`/`voxscope` databases (fresh schemas).
