# Running the unified platform locally

One backend (voice + RAG + observability) + one Next.js frontend + infra.

## 1. Infra (Postgres, Qdrant, Redis) — Docker
The app needs Postgres (5432), Qdrant (6333), Redis (6379). Existing containers:

```bash
docker start 58f7ae2057a2_rag-postgres-1 90fa60800514_rag-qdrant-1   # postgres + qdrant
# Redis on 6379 is already running; if not: docker start <your-redis>
```
(Fresh setup alternative: `docker compose up -d postgres qdrant redis` from this dir.)

## 2. Backend (FastAPI on :8000)
Must have real network access (LiveKit / Deepgram / Cartesia / Groq are external):

```bash
cd /home/hrash-raj/test/voice/voice-agent
PYTHONPATH=src uv run uvicorn main:app --host 0.0.0.0 --port 8000
```

### Voice engine (Pipecat vs LiveKit Agents)
The voice pipeline runs on either engine, chosen by `VOICE_ENGINE` in `.env`
(default in `.env` is now `livekit`). Both support RAG tools, the spoken filler,
stereo recording, transcripts, and observability:

```bash
VOICE_ENGINE=livekit  uv run uvicorn main:app --port 8000   # LiveKit Agents (current)
VOICE_ENGINE=pipecat  uv run uvicorn main:app --port 8000   # original Pipecat engine
```
Agents that use a knowledge base should use the **Llama 3.3 70B** model (set as
the default) — the 8B model is unreliable at tool calls.
Health check: `curl localhost:8000/health` → `{"status":"ok","modules":[...]}`.

## 3. Frontend (Next.js on :3000)
```bash
cd /home/hrash-raj/test/voice/voice-agent/frontend
npm install      # first time only
npm run dev
```
Open http://localhost:3000  (`NEXT_PUBLIC_API_URL` defaults to http://localhost:8000).

## 4. Voice worker (only for INBOUND calls)
Outbound calls run the pipeline inside the API process — no worker needed.
For inbound: `PYTHONPATH=src uv run python worker.py dev`

## Using the app
1. Sign up / log in at http://localhost:3000.
2. **Knowledge** → create a knowledge base, upload a doc (PDF/DOCX/TXT) or add a URL.
3. **Voice Agents** → create an agent, pick model **Llama 3.3 70B** (required for RAG/tools),
   and attach the knowledge base from the dropdown.
4. **Calls** → New Call → enter a number + pick the agent → Dial. Answer and talk.
5. **Calls** → open the call for the transcript + recording playback.
6. **Observability** → traces, per-turn latency waterfall, latency metrics.

## Recordings
Stereo WAV per call (left = caller, right = agent), served at:
`http://localhost:8000/recordings/<call_id>.wav`  (also linked from the call detail page).

## Notes
- Knowledge-base agents MUST use `llama-3.3-70b-versatile` (the 8B model is unreliable at tool calls).
- Postgres/Qdrant containers stop across reboots/days — `docker start` them before the backend.
