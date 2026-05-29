# Voice AI Agent Platform — Product Specification v1.0

**Status:** Active development  
**Owner:** Product  
**Last updated:** 2026-05-19  
**Stack:** Python FastAPI + Pipecat + Groq (Llama 4 Scout) + Deepgram Nova-3 + Cartesia Sonic-Turbo + LiveKit

---

## 1. Product Vision

Voice AI infrastructure is fragmented and expensive. Most developers who want to build a voice agent today must stitch together five or six vendors, write thousands of lines of orchestration code, and still end up paying $0.15–$0.40 per minute for production calls. That cost ceiling makes high-volume use cases — insurance outreach, appointment reminders, real-estate prospecting, BPO automation — economically impossible at scale. The Voice AI Agent Platform solves this by providing a single, opinionated, production-ready layer on top of the cheapest viable combination of speech and LLM providers (Deepgram + Groq + Cartesia), targeting a total cost under $0.04 per minute and end-to-end voice latency under 500 ms. Developers get a clean REST API and a configuration-first agent model; operators get a dashboard with live call monitoring, full transcripts, and per-call cost breakdowns.

Success looks like this: a developer who has never touched the platform makes a live outbound AI call within five minutes of reading the quickstart. An operator running 200,000 calls per month has replaced a meaningful fraction of their human agent capacity, with a cost per conversation that is 80–90 percent lower and a first-response latency that is imperceptible to callers. The platform is not trying to compete with Twilio or Bland on breadth of features — it wins on price, latency, and developer experience, and it stays focused on those three things until they are undeniably best-in-class.

---

## 2. Core User Journeys

### Journey 1: First Call in 5 Minutes

**User story:** As a developer, I want to go from sign-up to a live AI call in under 5 minutes so that I can evaluate the platform before investing any significant engineering effort.

**Onboarding steps:**

1. Sign up with email + password (no credit card required for first 10 minutes of calls).
2. Platform creates a default agent named "My First Agent" with a working system prompt, a pre-selected voice, and Groq Llama 4 Scout as the LLM — zero configuration required.
3. Dashboard shows a prominent "Make a Test Call" button that accepts a phone number.
4. User enters their own mobile number, clicks dial.
5. Phone rings within 3 seconds. AI agent answers and speaks the default greeting.
6. After the call, the transcript and cost appear in the Calls tab automatically.

**What "done" looks like:** The developer has spoken to their AI agent on a real phone call, seen the transcript, and seen a cost line item — all without writing a single line of code.

**Acceptance criteria:**

- [ ] Registration to first call completes in under 5 minutes on a fresh account with no prior setup.
- [ ] Default agent is created automatically on first login; no manual configuration required to reach "callable" state.
- [ ] Outbound call connects and AI speaks within 3 seconds of the API call being acknowledged.
- [ ] Transcript is visible in the dashboard within 30 seconds of call ending.
- [ ] End-to-end voice latency (user speaks → AI responds) is under 500 ms on the test call.
- [ ] If the call fails (bad number, SIP error), the UI shows a human-readable error — not a raw status code.

---

### Journey 2: Create a Custom Agent

**User story:** As a user, I want to configure an AI agent with a custom personality, voice, and behavior so that it sounds like it belongs to my product rather than a generic demo.

**Required fields:**

| Field | Description | Example |
|---|---|---|
| `name` | Internal label for the agent | "Appointment Reminder Bot" |
| `system_prompt` | Full persona and instructions for the LLM | "You are Maya, a friendly scheduler for Acme Dental..." |

**Optional fields (with smart defaults):**

| Field | Default | Notes |
|---|---|---|
| `voice_id` | Cartesia "Helpful Woman" voice | Platform shows a "Preview Voice" button before saving |
| `llm_model` | `meta-llama/llama-4-scout-17b-16e-instruct` | Exposed so power users can swap to a larger model |
| `llm_temperature` | `0.7` | Range 0.0–1.0 |
| `first_message` | `null` (agent waits for caller to speak first) | If set, agent speaks this line immediately on connect |
| `end_call_phrases` | `["goodbye", "talk to you later"]` | Words that trigger graceful hangup |
| `max_call_duration_seconds` | `600` (10 min) | Hard ceiling to prevent runaway cost |
| `language` | `en-US` | Passed to Deepgram STT |

**Smart defaults rationale:**

The LLM model and voice defaults are chosen to hit the platform's cost and latency targets out of the box. A developer who uses all defaults will pay approximately $0.04/min and get under 500 ms latency without ever touching advanced settings.

**Acceptance criteria:**

- [ ] An agent can be created with only `name` and `system_prompt`; all other fields default silently.
- [ ] Voice preview plays a 5-second audio clip using the selected `voice_id` before the user saves.
- [ ] System prompt supports up to 4,000 tokens without truncation.
- [ ] Saving an agent returns the full agent object including its assigned `id` within 500 ms.
- [ ] An agent can be edited at any time; changes take effect on the next call, not mid-call.
- [ ] Deleting an agent that has associated calls soft-deletes it (calls remain queryable); hard delete requires explicit confirmation.
- [ ] The agent list page shows each agent's total calls, total cost, and average latency.

---

### Journey 3: Make an Outbound Call

**User story:** As a user, I want to initiate an outbound call to a phone number using my configured agent so that I can automate outreach without managing telephony infrastructure.

**Call flow (what happens under the hood):**

1. `POST /calls/outbound` is received by the API.
2. API validates `to_number` format (E.164 required), resolves `agent_config_id` (defaults to user's primary agent if omitted).
3. API creates a call record in `pending` state and returns `{ call_id, status: "pending" }` immediately — the response is non-blocking.
4. A background task dials the number via the configured SIP trunk (Plivo / Telnyx / Twilio — provider-agnostic).
5. Status transitions: `pending` → `dialing` → `in_progress` → `completed` | `failed` | `no_answer` | `busy`.
6. On connect, the Pipecat pipeline starts: VAD detects speech → Deepgram transcribes → Groq generates response → Cartesia synthesizes → LiveKit delivers audio.
7. Call ends when: caller hangs up, agent detects an `end_call_phrase`, `max_call_duration_seconds` is hit, or `POST /calls/{id}/hangup` is called.
8. On end, the call record is finalized with duration, cost breakdown, and transcript.

**Success state:** Call record shows `status: completed`, transcript is populated, cost is non-zero.

**Failure states and handling:**

| State | Meaning | What the user sees |
|---|---|---|
| `no_answer` | Rang for 30s, nobody picked up | Status + timestamp |
| `busy` | Line busy or rejected | Status + SIP code |
| `failed` | SIP/network error | Status + human-readable error message |
| `invalid_number` | E.164 validation failed | Immediate 422 response with field-level error |

**Acceptance criteria:**

- [ ] `POST /calls/outbound` returns within 300 ms regardless of when the actual dial happens.
- [ ] The returned `call_id` is immediately queryable via `GET /calls/{id}`.
- [ ] Status updates are reflected in the dashboard within 2 seconds of the underlying state change.
- [ ] A call to an invalid number format returns HTTP 422 with `{ field: "to_number", message: "Must be E.164 format, e.g. +12125551234" }`.
- [ ] `POST /calls/{id}/hangup` terminates the call within 2 seconds.
- [ ] Concurrent outbound calls from the same account do not interfere with each other.
- [ ] `max_call_duration_seconds` is enforced; call auto-terminates and records `status: completed` with a `termination_reason: max_duration` flag.

---

### Journey 4: Review Call Performance

**User story:** As a user, I want to see what was said in a call and how much it cost so that I can debug agent behavior and track spend.

**Transcript view:**

- Conversation displayed as a chat-style thread, alternating `agent` / `caller` turns.
- Each turn shows: speaker label, text, start timestamp (relative to call start, e.g. `0:14`), and per-turn latency in ms (time from end of caller speech to start of agent response).
- Turns where latency exceeded 600 ms are highlighted in amber as a quality signal.
- Full transcript exportable as JSON or plain text.

**Cost breakdown (per call):**

| Line item | Provider | Calculation basis |
|---|---|---|
| STT | Deepgram Nova-3 | `duration_seconds / 60 × $0.0077` |
| LLM | Groq | `(input_tokens + output_tokens) × rate` |
| TTS | Cartesia Sonic-Turbo | `characters_synthesized × rate` |
| Telephony | SIP trunk | `duration_seconds / 60 × provider_rate` |
| **Total** | | Sum of above |

**Latency per turn:**

- P50 and P95 turn latency shown as a summary for the full call.
- Timeline chart: horizontal bar per turn, colored by component (STT / LLM / TTS).

**Acceptance criteria:**

- [ ] `GET /calls/{id}` returns a `turns` array where each object has `speaker`, `text`, `start_ms`, `end_ms`, `latency_ms`.
- [ ] Cost breakdown is itemized at the call level; total matches sum of components within ±1 cent.
- [ ] Transcript is available within 10 seconds of call ending.
- [ ] Turns with latency > 600 ms are flagged with `slow: true` in the API response.
- [ ] The transcript page renders calls up to 60 minutes (3,600 turns) without pagination.
- [ ] Export to JSON returns the full turns array, cost breakdown, and call metadata in a single file.

---

### Journey 5: Monitor Multiple Calls

**User story:** As an operator, I want to see all active calls and recent call history at a glance so that I can ensure operations are running normally without checking each call individually.

**Dashboard requirements:**

- **Live calls widget:** Shows count of currently active calls, list of up to 10 active calls with `to_number` (masked to last 4 digits), agent name, duration, and a "Hang Up" button per row.
- **Recent calls table:** Last 50 calls, sortable by time, duration, cost, and status. Columns: status badge, to number (masked), agent name, duration, total cost, transcript link.
- **Summary cards (top of dashboard):** Calls today, minutes today, cost today, average turn latency today.
- **Cost trend chart:** Daily spend for the last 30 days, line chart.

**Refresh behavior:**

- Active calls widget polls every 5 seconds automatically (no manual refresh needed).
- Recent calls table auto-refreshes every 30 seconds.
- Operator can force an immediate refresh via a refresh icon.
- Dashboard does not full-page reload on refresh; only the data updates.

**Acceptance criteria:**

- [ ] Active call count updates within 5 seconds of a new call connecting.
- [ ] "Hang Up" button on the live calls widget terminates the call within 2 seconds; the row disappears from the active list on the next poll.
- [ ] Recent calls table loads within 1 second for accounts with up to 10,000 historical calls.
- [ ] Summary cards show today's figures accurate to the minute.
- [ ] Phone numbers in all dashboard views are masked to last 4 digits by default; a "show full" toggle reveals them (audit-logged).
- [ ] Dashboard is usable on a 1280×800 display without horizontal scrolling.
- [ ] If the API is unreachable, the dashboard shows a "Connection error — retrying..." banner rather than a blank or broken state.

---

## 3. Feature Prioritization (ICE Scoring)

**Scoring:** Impact (1–10) × Confidence (1–10) × Ease (1–10), normalized to a 0–1000 scale. Higher = build sooner.

| Feature | Impact | Confidence | Ease | ICE Score | Decision | Rationale |
|---|---|---|---|---|---|---|
| API key management / authentication | 10 | 10 | 8 | 800 | **Build (Sprint 1)** | Without API keys, every integration requires JWT flows; this is a blocker for any external developer. |
| Webhook callbacks (call end notification) | 9 | 9 | 8 | 648 | **Build (Sprint 1)** | High-volume users cannot poll; webhooks are the primary integration primitive for CRM, scheduling, and analytics pipelines. |
| Voicemail detection and handling | 8 | 8 | 7 | 448 | **Build (Sprint 1)** | 30–50% of outbound calls hit voicemail; without AMD, cost is wasted and call records are meaningless. |
| Retry / no-answer handling | 7 | 8 | 7 | 392 | **Build (Sprint 2)** | Insurance and home-services users run retry campaigns as a core workflow; this is a table-stakes outbound feature. |
| Call recording + audio playback | 7 | 7 | 6 | 294 | **Build (Sprint 2)** | Quality assurance and compliance use cases require recordings; transcripts alone are insufficient for regulated industries. |
| Time-zone-aware scheduling | 6 | 8 | 6 | 288 | **Build (Sprint 2)** | TCPA compliance requires time-zone awareness for outbound calling; this is both a legal requirement and a trust signal. |
| Usage-based billing / invoicing | 8 | 6 | 5 | 240 | **Build (Sprint 3)** | Required for any paid tier, but the cost-tracking infrastructure already exists; this is mostly a Stripe integration and invoice UI. |
| Phone number provisioning | 6 | 6 | 5 | 180 | **Defer** | Operators can use existing numbers via SIP trunk; in-platform provisioning is a convenience feature, not a blocker. Revisit after billing. |
| Multi-language support (Spanish first) | 5 | 6 | 4 | 120 | **Defer** | Strong market signal for Spanish, but Deepgram Nova-3 already supports it at the API level; the work is prompt/voice catalog, not infrastructure. Defer until a paying customer requires it. |
| Call transfer to human agent | 4 | 5 | 3 | 60 | **Skip (v1)** | SIP transfer requires warm-handoff logic, hold music, and agent availability APIs — significant complexity for a feature most v1 users do not need yet. |

---

## 4. UX Principles

These ten principles are specific to voice AI dashboards and developer-facing platforms. They are not generic UX rules.

**1. Latency is a first-class metric, always visible.**
Every place a call appears — list row, detail page, summary card — shows turn latency. Developers chose this platform for speed; hiding that number is hiding the core value proposition.

**2. Phone numbers are PII. Treat them that way by default.**
Numbers are masked to the last 4 digits everywhere in the UI. Full reveal requires a deliberate action and is audit-logged. Do not make privacy opt-in.

**3. The transcript is the primary artifact, not a detail view.**
After a call ends, the transcript should be one click away from any call reference in the UI. It is the main debugging tool; bury it and the platform loses utility.

**4. Cost is always shown alongside quality.**
A transcript view without the cost breakdown next to it is incomplete. Developers and operators need to correlate "did this call work well?" with "how much did it cost?" in the same glance.

**5. Async operations must expose live status.**
Every action that is not synchronous (starting a call, uploading a prompt, provisioning a number) must show a visible status transition. The developer should never be left wondering if something worked.

**6. Error messages must tell you what to do, not just what failed.**
"SIP trunk unreachable" is useless. "SIP trunk unreachable — check that SIP_PROVIDER_URI in your environment is set correctly and the trunk is active in your carrier dashboard" is actionable.

**7. The API and the dashboard must be in parity.**
Every action achievable in the dashboard must be achievable via the API, and vice versa. If a feature exists only in one surface, it erodes trust in the platform's completeness.

**8. Defaults should produce a working system.**
A developer who accepts every default must end up with a functional, cost-optimal agent. Defaults are not placeholder values — they are the recommended production configuration.

**9. Volume operators need bulk views, not just per-call views.**
Users running 10,000 calls per day need aggregated views (by agent, by time window, by status) and bulk operations (cancel all active calls, export all transcripts). Single-call detail views are necessary but not sufficient.

**10. The developer experience degrades gracefully under rate limits and partial failures.**
If Deepgram is degraded, the dashboard should show which calls were affected and surface Deepgram's status page link — not just show empty transcripts. Infrastructure failures should be surfaced transparently, not silently swallowed.

---

## 5. API Design Review

The following issues were identified in the proposed API surface. Each entry describes what is wrong, what to change, and why it matters for developer experience (DX).

---

**Issue 1: `POST /calls/outbound` — missing synchronous acknowledgment contract**

- **What's wrong:** The endpoint signature `{ to_number, agent_config_id? }` does not document what the response body looks like. Developers do not know whether to expect a call object, a job ID, or a boolean.
- **What to change:** Document (and enforce) that the response is always `{ call_id: uuid, status: "pending", created_at: iso8601 }` and that the HTTP status is 202 Accepted (not 200), because the actual dialing is asynchronous.
- **Why it matters:** 202 vs 200 is a semantic signal that tells developers not to treat the response as a completed operation. Missing this causes polling bugs where developers assume the call is live when it is still pending.

---

**Issue 2: `GET /calls` — no filtering, only pagination**

- **What's wrong:** `?page=1&limit=20` supports pagination but not filtering by status, agent, date range, or direction (inbound/outbound). A user with 50,000 calls cannot find anything useful.
- **What to change:** Add query parameters: `?status=completed&agent_id=uuid&from=2026-01-01&to=2026-01-31&direction=outbound`. Keep pagination. Add `total_count` to the response envelope so clients can render pagination controls without a separate count query.
- **Why it matters:** High-volume operators — the platform's target segment — will filter by status and date range constantly. Forcing them to download all pages to find failed calls is a dealbreaker.

---

**Issue 3: `GET /calls/{id}` — `turns[]` is ambiguous**

- **What's wrong:** The spec says the response "includes turns[]" but does not define the shape of a turn object. Developers will build against whatever they see in development, which may not match production.
- **What to change:** Define the turn schema explicitly in the API docs and enforce it in the response: `{ id, speaker: "agent"|"caller", text, start_ms, end_ms, latency_ms, slow: bool }`. Include the cost breakdown as a top-level `cost` object, not embedded in `turns`.
- **Why it matters:** Ambiguous response shapes are the most common source of integration bugs. Callers will ship code against the undocumented shape and break when it changes.

---

**Issue 4: `POST /calls/{id}/hangup` — verb in URL path is an anti-pattern**

- **What's wrong:** REST conventions use HTTP verbs + nouns, not verbs in the path. `/calls/{id}/hangup` mixes metaphors and makes the API surface harder to discover.
- **What to change:** Replace with `PATCH /calls/{id}` with body `{ status: "terminated" }`. This makes the calls resource consistent with how the agents resource is mutated, and it naturally extends to other status transitions (e.g., putting a call on hold) without adding new endpoints.
- **Why it matters:** Inconsistency in URL patterns increases the time developers spend reading docs instead of building. A single mutation pattern across all resources is self-documenting.

---

**Issue 5: `POST /agents` — `voice_id` and `llm_model` are optional but opaque**

- **What's wrong:** The endpoint accepts `voice_id?` and `llm_model?` but does not tell the developer what valid values are. There is no `/voices` or `/models` endpoint to enumerate options.
- **What to change:** Add `GET /voices` → returns list of available `{ voice_id, name, provider, preview_url }`. Add `GET /models` → returns list of available `{ model_id, provider, cost_per_token, latency_tier }`. Also add a `GET /agents/defaults` endpoint that returns the platform's recommended defaults as a reference.
- **Why it matters:** Developers should not need to read a separate blog post to find valid voice IDs. Discoverable APIs reduce support burden and integration time.

---

**Issue 6: `POST /webhooks/livekit` — internal webhook exposed on public surface**

- **What's wrong:** The LiveKit room event webhook is surfaced as a first-class public API endpoint. This is an internal infrastructure hook, not a developer-facing API. It has no authentication boundary documented.
- **What to change:** Move to `/internal/webhooks/livekit` and enforce that it is only callable from LiveKit's IP range (whitelist in the SIP/WebRTC layer, not in application code). It should not appear in the public API reference at all.
- **Why it matters:** Exposing internal webhooks on the public surface invites spoofed requests. If an attacker can POST to `/webhooks/livekit` with a fake room-ended event, they can close calls fraudulently and corrupt cost data.

---

**Issue 7: No versioning prefix on the call/agent endpoints**

- **What's wrong:** Endpoints are listed as `/calls`, `/agents`, `/health` with no `/v1/` prefix, while the AGENTS.md schema shows the existing implementation uses `/api/v1/`. These two surfaces are inconsistent.
- **What to change:** Standardize on `/api/v1/calls`, `/api/v1/agents`, etc. across all documentation and implementation. The `/health` endpoint is the only acceptable exception — it should live at `/health` (no version prefix) since it is consumed by load balancers, not application clients.
- **Why it matters:** Shipping a version-less public API means any breaking change requires a flag day. A consistent `/api/v1/` prefix is the minimum viable versioning strategy.

---

## 6. Success Metrics

### Metric 1: Time to First Call (T2FC)

- **How to measure:** Instrument the onboarding flow. Log `signup_at` and `first_call_connected_at` per user. Compute the delta.
- **Target:** Median T2FC under 5 minutes; P95 under 15 minutes.
- **Why it matters:** T2FC is the single strongest signal for developer experience quality. If the median developer cannot make a call in 5 minutes, the top-of-funnel is broken regardless of how good the platform is at scale.

### Metric 2: Cost per Minute (Fully Loaded)

- **How to measure:** Sum all billed costs (STT + LLM + TTS + telephony) across all calls in a billing period. Divide by total call minutes. Track weekly.
- **Target:** Under $0.05 per minute at median (P50). Alert if P75 exceeds $0.08.
- **Why it matters:** This is the platform's primary differentiator. Drift above $0.08/min erodes the competitive moat and needs immediate investigation (model upgrades, STT overages, TTS character spikes).

### Metric 3: End-to-End Turn Latency (P95)

- **How to measure:** For every call, record `latency_ms` per turn (end of caller speech to start of agent speech). Aggregate P95 across all turns in a 24-hour window.
- **Target:** P95 turn latency under 600 ms. Alert if P95 exceeds 800 ms.
- **Why it matters:** Latency above 800 ms is perceptually awkward in phone conversations. If P95 degrades, callers will hang up or assume the line is dead — directly impacting operator conversion rates.

### Metric 4: API Error Rate

- **How to measure:** Count all 4xx and 5xx responses from `/api/v1/*` endpoints. Divide by total API requests. Measure per 5-minute window.
- **Target:** 5xx rate under 0.1%. 4xx rate under 2% (high 4xx often indicates DX problems, not server bugs).
- **Why it matters:** A high 5xx rate means calls are failing silently or not starting at all. A high 4xx rate usually means the API surface is confusing — developers are sending malformed requests because the docs or defaults are misleading.

### Metric 5: Weekly Active Developers (WAD)

- **How to measure:** Count distinct user IDs that made at least one API call (authenticated, non-health endpoint) in a rolling 7-day window.
- **Target:** 20% week-over-week growth for the first 3 months post-launch; stabilize to 10% thereafter.
- **Why it matters:** WAD is the leading indicator of retention. A developer who comes back in week 2 is a developer who found value. This metric reveals whether the platform is a toy people try once or infrastructure they actually depend on.

---

## 7. Non-Goals (What We Are NOT Building in v1)

**1. Multi-tenant team accounts and role-based access control (RBAC)**
Every user has their own isolated agent and call namespace. Shared accounts, team seats, and admin/member roles are out of scope. Rationale: RBAC adds significant auth complexity; the v1 user is a solo developer or a single operator, not an enterprise with multiple stakeholders accessing the same account.

**2. In-platform phone number provisioning**
Users bring their own SIP trunk and phone numbers. The platform configures the trunk but does not sell or manage numbers. Rationale: Number provisioning requires carrier partnerships, porting workflows, and regulatory compliance per country. It is a significant product in its own right.

**3. Call recording storage and retrieval**
The platform tracks transcripts and costs but does not store audio files. Recording URLs can be stored if the user's SIP provider provides them, but we do not record, transcode, or serve audio. Rationale: Audio storage adds substantial infrastructure cost (S3/GCS), GDPR/CCPA data-retention obligations, and compliance surface area that would distract from the core latency/cost mission.

**4. Visual IVR / call flow builder (drag-and-drop)**
Agent behavior is configured entirely through system prompts. There is no drag-and-drop call flow builder, branching logic editor, or visual node graph. Rationale: The LLM handles branching naturally via prompt instructions. Adding a visual flow layer before the LLM-native approach is proven would fragment the UX and add a second mental model for users to learn.

**5. Inbound call routing and IVR menus**
While inbound call handling is in scope (an agent can answer an inbound call), routing logic — press 1 for sales, press 2 for support, queue management, agent availability — is out of scope. Rationale: This is a contact center product, not a voice AI infrastructure product. The target user deploys one agent, one behavior; routing systems serve a different use case.

**6. Custom voice cloning**
Users select from Cartesia's voice catalog. Custom voice cloning (uploading a reference audio and generating a new voice identity) is out of scope. Rationale: Voice cloning requires additional Cartesia API tiers, consent management workflows, and abuse prevention — all significant surface area for a feature that does not serve the cost/latency thesis.

**7. Native mobile SDK**
There is no iOS or Android SDK. The platform is a REST API and a web dashboard. Rationale: The target users are backend developers and operators building automation workflows — not mobile app developers. An SDK would be premature before the API surface is stable.

**8. CRM and calendar integrations (Salesforce, HubSpot, Google Calendar)**
There are no native integrations with any third-party SaaS tools. Users who need CRM or calendar connectivity use webhooks to build their own integration. Rationale: Native integrations are maintenance obligations. Webhooks give the same result with zero ongoing support cost. Premature integrations also fragment the product roadmap and pull engineering away from core infrastructure work.
