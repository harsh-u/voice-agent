# Convoxio — Product Overview & Feature Inventory

> A product-owner's map of **what the platform is, every feature we ship today, how the
> CRM ties it all together, and where the gaps are** — so we can plan the roadmap from a
> shared, accurate baseline.
>
> _This reflects the codebase as built (one unified backend + Next.js frontend). It
> complements the older, narrative `PRODUCT.md`; where they differ, this file is current._

---

## 1. What Convoxio is

**Convoxio is a multi-channel conversational sales & support platform.** It unifies three
things that are usually separate products into **one app, one login, one database**:

1. **AI Voice Agents** — automated inbound/outbound phone calls that talk to real people.
2. **WhatsApp CRM** — a full contact/conversation/sales-pipeline CRM with WhatsApp as the
   messaging channel.
3. **Knowledge + Observability** — a RAG knowledge base the voice agents answer from, and
   call observability (latency/cost/transcripts) to monitor quality.

The wedge: **a business can run AI phone agents and WhatsApp conversations against the same
contact record, and see everything (calls, chats, deals, costs) in one place.**

---

## 2. Who it's for

- **Outbound sales / SDR teams** — AI agents dial leads, qualify, and log outcomes to the CRM.
- **Inbound support / reception** — AI answers calls, looks up answers from a knowledge base.
- **WhatsApp-first businesses** — manage customer chats, broadcasts, and a sales pipeline.
- **Ops/QA** — review call transcripts, recordings, latency and cost per call.

---

## 3. Architecture at a glance

| Layer | What |
|---|---|
| **Backend** | One FastAPI app (`:8000`) with modules mounted together: voice/CRM (`/`), RAG (`/rag`), observability (`/observability`). |
| **Frontend** | One Next.js dashboard (`:3000`) — all features in one UI. |
| **Database** | One PostgreSQL DB; RAG tables in schema `rag`, observability in schema `obs`, everything else in `public`. |
| **Voice pipeline** | Pluggable engine — **LiveKit Agents** (current default) or **Pipecat** — STT (Deepgram) → LLM (Groq Llama-3.3-70B) → TTS (Cartesia), over **LiveKit** rooms + **SIP** telephony. |
| **Infra** | Postgres, Qdrant (vectors), Redis (RAG cache). |
| **Auth** | Single JWT identity (SSO) shared across all modules. |

**Channels today: WhatsApp (Meta Business API) + Voice (SIP/LiveKit).** The CRM is the shared
spine both channels write to.

---

## 4. Feature areas (detailed)

Each section: **What it does → How it works → CRM tie-in → Status → Gaps.**
Status legend: ✅ shipped · 🟡 partial/beta · ⛔ not built.

### 4.1 WhatsApp Messaging  ✅
- **What:** Two-way WhatsApp messaging via the **Meta WhatsApp Business Cloud API**.
- **How:** Per-account `WhatsAppConfig` (phone number ID, WABA ID, access token encrypted
  at rest with AES-256-GCM). Client (`whatsapp/meta_api.py`) supports: **send text, media
  (image/video/audio/document), interactive messages, template messages, emoji reactions,
  mark-as-read, media download**. Inbound messages + delivery/read receipts arrive via the
  Meta **webhook** (`/whatsapp` webhook with HMAC verification) and are persisted as `Message`
  rows (direction inbound/outbound; status pending→sent→delivered→read→failed).
- **Templates:** create / sync-from-Meta / edit / delete approved **message templates**
  (`/whatsapp/templates`, statuses pending/approved/etc.).
- **CRM tie-in:** every message belongs to a `Conversation` which belongs to a `Contact`.
- **Endpoints:** `/whatsapp/config`, `/whatsapp/send`, `/whatsapp/templates(/sync)`, `/whatsapp/media/{id}`, webhook `/whatsapp`.
- **Gaps:** ⛔ no multi-number/WABA per account, ⛔ no template analytics, 🟡 interactive
  (button/list) builder is API-only (no rich UI), ⛔ no WhatsApp Flows (Meta) support,
  ⛔ no other channels yet (SMS, Instagram, Messenger, email).

### 4.2 CRM — Contacts & Tags  ✅
- **What:** Central contact records with custom fields + labels.
- **How:** `Contact` (phone E.164, name, email, company, `custom_fields` JSON), unique per
  (user, phone). `Tag` + `ContactTag` M2M for segmentation (name + color). Full CRUD +
  add/remove tag + **per-contact call history** (`/contacts/{id}/calls`).
- **CRM tie-in:** the hub object — conversations, deals, calls, messages all reference a contact.
- **Gaps:** ⛔ no bulk import/export (CSV), ⛔ no dedup/merge, ⛔ no contact activity timeline
  view (data exists, no unified view), ⛔ no custom-field schema management UI, ⛔ no lead scoring.

### 4.3 CRM — Conversations & Inbox  ✅
- **What:** A unified inbox of conversation threads.
- **How:** `Conversation` per contact (status open/pending/closed, `assigned_to` a user).
  `Message` history per thread; **live updates via SSE** (`/conversations/{id}/sse`). Internal
  **notes** are a message type (`note`) — e.g. auto-written post-call summaries (see §5).
- **CRM tie-in:** threads link contact ↔ messages ↔ assignee; calls drop notes here.
- **Endpoints:** `/conversations`, `/conversations/contact/{id}`, `/messages/{conversationId}`, `/inbox`.
- **Gaps:** ⛔ no team collaboration (mentions, internal-only replies beyond notes), ⛔ no
  canned replies/snippets, ⛔ no SLA/first-response timers, ⛔ no conversation search/filter
  beyond status, ⛔ no AI-assist (draft replies/summaries) in the inbox.

### 4.4 CRM — Sales Pipelines & Deals  ✅
- **What:** Kanban-style sales pipelines.
- **How:** `Pipeline` → `PipelineStage` (ordered, colored) → `Deal` (title, value, close_date,
  status open/won/lost, linked to a contact). Full CRUD for pipelines, stages, deals.
- **CRM tie-in:** deals reference contacts; pipeline value rolls up to the dashboard.
- **Gaps:** ⛔ no automation on stage-change (e.g. auto-call when moved to "Hot"), ⛔ no deal
  activity log, ⛔ no forecasting/weighted pipeline, ⛔ no products/line-items, ⛔ drag-drop
  is UI-only (no server-side stage-change hooks).

### 4.5 Broadcasts (bulk messaging)  ✅
- **What:** Send a template message to many contacts at once.
- **How:** `Broadcast` (name, template, status draft→scheduled→sending→completed/failed) with
  per-recipient tracking (`BroadcastRecipient`, delivery status). Create / send / delete.
- **CRM tie-in:** recipients are contacts; results visible per recipient.
- **Endpoints:** `/broadcasts`, `/broadcasts/{id}/send`.
- **Gaps:** 🟡 scheduling model exists but no scheduler/queue worker proven for large sends,
  ⛔ no audience builder by tag/segment, ⛔ no rate-limit/throttling controls, ⛔ no
  open/click/response analytics, ⛔ no A/B testing.

### 4.6 Automations  🟡
- **What:** Trigger → steps workflows (e.g. "on message received, do X").
- **How:** `Automation` (trigger_type, default `message_received`; `trigger_config` JSON;
  `steps` JSON; is_active) with `AutomationLog` per execution. CRUD + duplicate.
- **CRM tie-in:** intended to act on contacts/conversations/deals.
- **Status:** data model + CRUD shipped; **execution engine is partial** (only
  `message_received` trigger wired; step/action catalog is thin).
- **Gaps:** ⛔ rich trigger set (call ended, deal stage changed, tag added, schedule/cron),
  ⛔ action catalog (send template, add/remove tag, create/move deal, start a call, HTTP
  webhook, branch/condition, delay), ⛔ no run history UI, ⛔ no test/dry-run.

### 4.7 Flows (visual conversation builder)  🟡 Beta
- **What:** Drag-and-drop builder for conversation trees.
- **How:** `Flow` (nodes + edges JSON, is_active), `FlowRun` per active execution, built with
  `@xyflow/react`. Node types today: **start, end, message, question, condition**; ships with
  **flow templates** and client-side validation/fallback logic.
- **CRM tie-in:** runs against a contact's conversation.
- **Status:** builder + validation + templates shipped (flagged **Beta** in the UI); runtime
  execution is early.
- **Gaps:** ⛔ richer nodes (buttons/list, API/webhook call, jump-to-flow, wait, set-field,
  hand-off-to-human, start-voice-call), ⛔ analytics per node, ⛔ versioning, ⛔ no voice-flow
  parity (flows are WhatsApp-only).

### 4.8 Voice AI Agents  ✅
- **What:** Configurable AI personas that handle phone calls.
- **How:** `AgentConfig` (name, system prompt, Cartesia voice, Groq model, SIP trunk, attached
  **knowledge base**). UI to create/edit, pick from curated **voices** and **models**
  (`/agents/options/voices|models`). Native **tool/function calling**: `query_knowledge_base`
  (RAG), `end_call`, `transfer_to_human`.
- **CRM tie-in:** an agent runs a `Call` tied to a `Contact`.
- **Gaps:** ⛔ no per-agent analytics/scorecards, ⛔ no A/B of prompts/voices, ⛔ transfer is
  acknowledged but **no real warm-transfer/DTMF** yet, ⛔ no multi-lingual config UI, ⛔ no
  agent-level guardrails/safety config, ⛔ no scheduled/drip call campaigns.

### 4.9 Calls (inbound & outbound)  ✅
- **What:** Real phone calls over SIP, fully recorded and transcribed.
- **How:** **Outbound** (`/calls/outbound`) dials a number via LiveKit SIP **after the agent
  joins the room** (avoids ghost/missed calls); **inbound** dispatched via the LiveKit worker.
  Each call: live status (dialing→active→completed/failed), **per-turn transcript** with
  latency, **stereo recording** (caller left / agent right) at `/recordings/{id}.wav`,
  **cost** computed from per-minute STT/LLM/TTS/telephony rates. Live "active calls" + hangup.
- **CRM tie-in:** call links to a contact; **a post-call note (with transcript snippet) is
  auto-written into the contact's WhatsApp conversation** (true cross-channel — see §5).
- **Endpoints:** `/calls/outbound`, `/calls`, `/calls/{id}`, `/calls/{id}/hangup`, `/calls/active`.
- **Gaps:** ⛔ no IVR/menu, ⛔ no voicemail detection, ⛔ no call queueing/concurrency caps UI,
  ⛔ recording time-alignment is approximate, ⛔ no live call monitoring/barge-in, ⛔ no
  call-disposition/outcome capture into deals.

### 4.10 Knowledge Base / RAG (agent feature)  ✅
- **What:** Documents the voice agent can answer from, in real time.
- **How:** Create **knowledge bases**, upload documents (**PDF/DOCX/TXT**) or **ingest a URL**;
  chunked + embedded locally (fastembed `bge-small`) into **Qdrant** with hybrid (dense+sparse)
  retrieval and Redis caching. Attaching a KB to an agent auto-provisions a managed key; on a
  call the agent calls `query_knowledge_base` and answers from retrieved context (verified live:
  correct grounded answers, spoken "let me check…" filler to mask lookup latency).
- **CRM tie-in:** indirect — powers the agent that serves contacts.
- **Endpoints:** `/rag/knowledge-bases…`, `/rag/v1/query`; agent-scoped proxy `/agents/{id}/knowledge/documents`.
- **Gaps:** 🟡 **single shared workspace** (all logins see the same KBs/keys — no per-user
  isolation), ⛔ no re-index/versioning, ⛔ no citations surfaced to the user, ⛔ URL ingest
  blocked without outbound DNS, ⛔ no scheduled re-crawl, ⛔ KB not yet usable in WhatsApp flows.

### 4.11 Observability  ✅
- **What:** Per-call latency/cost/transcript forensics, in-app.
- **How:** Every call ships a **trace** with **turns** and **spans** (STT/LLM/TTS/telephony) to
  the `/observability` module; UI shows a **traces list**, a **trace detail with a per-turn
  span waterfall**, and **latency metrics** (p50/p95/p99 per component) plus cost. Background
  rollups + retention. `framework` tag records which engine (livekit/pipecat) ran the call.
- **CRM tie-in:** trace_id = call_id, so observability ↔ CRM call records line up.
- **Gaps:** 🟡 single shared project (no per-user isolation), ⛔ no alerting/thresholds,
  ⛔ no dashboards/saved views, ⛔ STT/TTS spans are coarse, ⛔ no error-rate or
  word-error/quality metrics, ⛔ no export.

### 4.12 Dashboard & Analytics  ✅
- **What:** A single KPI overview.
- **How:** `/dashboard/metrics` returns: conversations today, open conversations, messages
  today, deals open/won, pipeline value, calls today, active calls, avg call duration, today's
  voice spend, answer rate, outbound calls today.
- **Gaps:** ⛔ no date-range/trend charts, ⛔ no per-agent/per-user breakdowns, ⛔ no funnel/
  conversion analytics, ⛔ no exports/scheduled reports.

### 4.13 Accounts & Auth  ✅
- **What:** Email/password accounts with JWT, single sign-on across all modules.
- **How:** register/login/refresh/me; roles `admin`/`agent`; one JWT validated by the RAG and
  observability modules via auto-provisioned shadow users.
- **Gaps:** ⛔ **no real multi-tenancy/workspaces** (voice agents, KBs, and obs are effectively
  global/single-workspace), ⛔ no team/role management UI, ⛔ no SSO/OAuth, ⛔ no billing/plans/
  usage metering, ⛔ no audit log, ⛔ no password reset flow wired end-to-end.

---

## 5. How the CRM is the backbone (and why it matters)

The CRM isn't a side-module — it's the **shared contact graph** every channel writes to:

- **One contact, every interaction:** `Contact` → conversations (WhatsApp messages), calls
  (voice), and deals (sales) all hang off the same record.
- **Cross-channel today:** when a **voice call** ends, the system **auto-writes a note with a
  transcript snippet into that contact's WhatsApp conversation** (`_post_call_note`). That's the
  seed of a true omni-channel timeline.
- **Pipeline value & activity** roll up into the dashboard.

**Why this is the strategic asset:** because calls and chats already converge on the contact,
future features (lead scoring, omni-channel timeline, "call this lead from the deal card",
automation triggers on any channel event) are **integrations of existing data**, not new plumbing.

**What's missing to make the CRM the true backbone:**
- ⛔ A single **contact 360 / activity timeline** UI (calls + chats + deals + notes in one view).
- ⛔ **Cross-object automation** (e.g. deal moved → trigger a call; call outcome → update deal).
- ⛔ **Import/export & dedupe** to get data in/out.
- ⛔ **Per-workspace isolation** so multiple teams/customers can use it safely.

---

## 6. Feature matrix — Have vs Missing (roadmap input)

| Area | Have ✅ | Notably missing ⛔ |
|---|---|---|
| WhatsApp | 2-way msgs, media, templates, interactive, webhooks | multi-number, template analytics, other channels |
| Contacts | CRUD, tags, custom fields, call history | import/export, dedupe, 360 timeline, lead scoring |
| Inbox/Convos | threads, SSE live, notes, assignment | canned replies, AI-assist, search, SLA timers |
| Pipelines/Deals | pipelines, stages, deals, won/lost | stage-change automation, forecasting, line-items |
| Broadcasts | bulk template send, per-recipient status | segment builder, scheduling at scale, analytics |
| Automations 🟡 | model + CRUD + `message_received` | trigger/action catalog, run history, branching |
| Flows 🟡 | visual builder, templates, validation | rich nodes, runtime, analytics, voice flows |
| Voice agents | config, voices, models, tools, RAG | per-agent analytics, real transfer, campaigns |
| Calls | in/outbound, transcripts, recording, cost | IVR, voicemail, live monitor, disposition→deal |
| Knowledge/RAG | KBs, docs, URL, hybrid retrieval, live Q&A | per-user isolation, citations, re-index |
| Observability | traces, waterfall, latency, cost | alerting, dashboards, isolation, quality metrics |
| Dashboard | core KPIs | trends, breakdowns, funnels, exports |
| Accounts | JWT SSO, roles | multi-tenancy, billing, team mgmt, audit log |

---

## 7. Tech stack & deployment

- **Backend:** Python 3.12, FastAPI, SQLAlchemy 2 (async), Pydantic. Voice via LiveKit Agents
  (or Pipecat) + Deepgram/Groq/Cartesia/Silero.
- **Frontend:** Next.js 16, React 19, Tailwind, shadcn/ui, `@xyflow/react`.
- **Data:** PostgreSQL (public/rag/obs schemas), Qdrant, Redis.
- **Telephony:** LiveKit Cloud + SIP trunk (Plivo).
- **Run/Deploy:** see `RUN.md` (local) and `docker-compose.yml` (postgres/qdrant/redis/api/
  worker/frontend). Engine switch via `VOICE_ENGINE=livekit|pipecat`.

---

## 8. Suggested roadmap priorities (PO view)

**P0 — make the core trustworthy as a product**
1. **Multi-tenancy / workspaces** (isolate contacts, KBs, traces, agents per account).
2. **Contact 360 timeline** (the omni-channel payoff; data already exists).
3. **Cross-object automations** (call ended → update deal; deal stage → trigger call/message).

**P1 — depth in what we have**
4. Automation trigger/action catalog + run history; promote Flows out of beta with a runtime.
5. Inbox AI-assist (draft replies, summaries) and canned replies.
6. Contact import/export + dedupe.
7. Broadcast segment builder + scheduling + analytics.

**P2 — voice & insight maturity**
8. Real warm transfer / IVR / voicemail detection; per-agent scorecards.
9. Observability alerting + saved dashboards; RAG citations + re-index.
10. Billing/usage metering and plans.

---

_Last updated by the team while consolidating the platform onto a single unified backend with
the LiveKit voice engine. Keep this file current as features land._
