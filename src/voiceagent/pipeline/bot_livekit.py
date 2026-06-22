"""LiveKit Agents implementation of the voice pipeline.

Drop-in alternative to pipeline/bot.py (Pipecat). Exposes the SAME
``run_pipeline(...)`` signature so runner.py is engine-agnostic; selected via
the ``VOICE_ENGINE=livekit`` flag (see config.py / runner.py).

Uses LiveKit Agents (AgentSession + Agent + function_tool) with the Deepgram,
Groq, Cartesia and Silero plugins. Parity with the Pipecat engine:
  - tools: query_knowledge_base (RAG) with immediate spoken filler, end_call, transfer
  - greeting spoken when the SIP leg answers; filler + greeting recorded as turns
  - per-turn transcript + response latency via conversation_item_added
  - outbound: dial the SIP leg after the bot has joined the room
Recording is best-effort and currently a no-op for this engine (see note below).
"""
from __future__ import annotations

import asyncio
import audioop
import contextlib
import time
import wave
from pathlib import Path
from typing import Any, Awaitable, Callable

from loguru import logger
from livekit import rtc
from livekit.agents import Agent, AgentSession, RoomInputOptions
from livekit.agents.utils import http_context
from livekit.plugins import cartesia, deepgram, groq, silero

from voiceagent.agent.tools import build_livekit_tools
from voiceagent.config import settings

RECORDINGS_DIR = Path("recordings")
RECORDINGS_DIR.mkdir(exist_ok=True)

_REC_SR = 24000  # target sample rate for the stereo recording


class _StereoRecorder:
    """Builds a stereo WAV (left=user, right=bot) from LiveKit audio frames.

    Frames from each side are normalized to mono @ _REC_SR and placed by
    wall-clock arrival time relative to the first frame, so the two channels
    stay roughly aligned. Best-effort — mirrors the Pipecat recording contract
    (recordings/{call_id}.wav, then on_recording_saved).
    """

    def __init__(self) -> None:
        self.left = bytearray()
        self.right = bytearray()
        self._t0: float | None = None
        self._user_state = None
        self._bot_state = None

    def _place(self, buf: bytearray, t: float) -> None:
        if self._t0 is None:
            self._t0 = t
        target = max(0, int((t - self._t0) * _REC_SR)) * 2
        if len(buf) < target:
            buf.extend(b"\x00" * (target - len(buf)))

    def _norm(self, frame, which: str) -> bytes:
        pcm = bytes(frame.data)
        ch = getattr(frame, "num_channels", 1) or 1
        sr = getattr(frame, "sample_rate", _REC_SR) or _REC_SR
        if ch > 1:
            pcm = audioop.tomono(pcm, 2, 0.5, 0.5)
        if sr != _REC_SR:
            if which == "user":
                pcm, self._user_state = audioop.ratecv(pcm, 2, 1, sr, _REC_SR, self._user_state)
            else:
                pcm, self._bot_state = audioop.ratecv(pcm, 2, 1, sr, _REC_SR, self._bot_state)
        return pcm

    def add_user(self, frame, t: float) -> None:
        pcm = self._norm(frame, "user")
        self._place(self.left, t)
        self.left.extend(pcm)

    def add_bot(self, frame, t: float) -> None:
        pcm = self._norm(frame, "bot")
        self._place(self.right, t)
        self.right.extend(pcm)

    def write(self, path: Path) -> bool:
        n = max(len(self.left), len(self.right))
        if n == 0:
            return False
        n -= n % 2  # whole 16-bit samples
        left = bytes(self.left[:n]) + b"\x00" * (n - len(self.left))
        right = bytes(self.right[:n]) + b"\x00" * (n - len(self.right))
        inter = bytearray(n * 2)
        inter[0::4] = left[0::2]
        inter[1::4] = left[1::2]
        inter[2::4] = right[0::2]
        inter[3::4] = right[1::2]
        with wave.open(str(path), "wb") as wf:
            wf.setnchannels(2)
            wf.setsampwidth(2)
            wf.setframerate(_REC_SR)
            wf.writeframes(bytes(inter))
        return True


def _cartesia_model() -> str:
    """The LiveKit Cartesia plugin expects sonic-2/sonic-3, not Pipecat's
    date-stamped model names (e.g. sonic-2024-10-19 / sonic-turbo)."""
    m = (settings.cartesia_model or "").strip()
    if not m or m.startswith("sonic-2024") or m in ("sonic-turbo", "sonic"):
        return "sonic-2"
    return m


@contextlib.asynccontextmanager
async def _ensure_http_context():
    """LiveKit plugins need a shared aiohttp session from an http context.

    The agent worker (inbound) sets this up automatically; the outbound path
    runs the session in the API process (no job context), so we open one here.
    Only open if none exists, to avoid double-wrapping the worker path.
    """
    try:
        http_context.http_session()  # raises if no context is active
        yield
    except Exception:
        async with http_context.open():
            yield


async def run_pipeline(
    call_id: str,
    room_name: str,
    bot_token: str,
    system_prompt: str,
    voice_id: str,
    llm_model: str,
    on_turn_end: Callable[[str, str, int | None], Any] | None = None,
    on_recording_saved: Callable[[str], Awaitable[None]] | None = None,
    rag_api_key: str | None = None,
    to_number: str | None = None,
) -> None:
    """Entry point: ensure a LiveKit http context, then run the session."""
    async with _ensure_http_context():
        await _run_session(
            call_id=call_id,
            room_name=room_name,
            bot_token=bot_token,
            system_prompt=system_prompt,
            voice_id=voice_id,
            llm_model=llm_model,
            on_turn_end=on_turn_end,
            on_recording_saved=on_recording_saved,
            rag_api_key=rag_api_key,
            to_number=to_number,
        )


async def _run_session(
    call_id: str,
    room_name: str,
    bot_token: str,
    system_prompt: str,
    voice_id: str,
    llm_model: str,
    on_turn_end: Callable[[str, str, int | None], Any] | None = None,
    on_recording_saved: Callable[[str], Awaitable[None]] | None = None,
    rag_api_key: str | None = None,
    to_number: str | None = None,
) -> None:
    """Run a LiveKit Agents voice session connected to a LiveKit room.

    For outbound calls (``to_number`` set) the bot joins the room first, then
    dials the SIP leg (dial-after-connect), and greets when the callee answers.
    For inbound the SIP caller is already in the room.
    """
    # --- Plugins ---
    stt = deepgram.STT(
        model=settings.deepgram_model,
        api_key=settings.deepgram_api_key,
        language="en-US",
        smart_format=True,
        interim_results=True,
    )
    llm = groq.LLM(model=llm_model or settings.groq_model, api_key=settings.groq_api_key)
    tts = cartesia.TTS(
        api_key=settings.cartesia_api_key,
        model=_cartesia_model(),
        voice=voice_id or settings.cartesia_voice_id,
    )
    vad = silero.VAD.load()

    # Optional semantic turn detection (better interruption handling). Guarded:
    # the plugin downloads a model on first use, so never let it break the call.
    turn_detection = None
    try:
        from livekit.plugins.turn_detector.multilingual import MultilingualModel
        turn_detection = MultilingualModel()
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning(f"[livekit] turn detector unavailable, using VAD endpointing: {exc}")

    # --- Latency tracking + turn recording ---
    _turn_start: list[float | None] = [None]

    async def _record_turn(role: str, text: str, latency_ms: int | None) -> None:
        if on_turn_end and text and text.strip():
            await on_turn_end(role, text.strip(), latency_ms)

    # --- Tools / Agent / Session ---
    tools = build_livekit_tools(on_turn_end=_record_turn, rag_api_key=rag_api_key)
    agent = Agent(instructions=system_prompt, tools=tools)
    session = AgentSession(
        stt=stt,
        llm=llm,
        tts=tts,
        vad=vad,
        turn_detection=turn_detection,
        # Defense-in-depth: strip any markdown/tool markup before TTS.
        tts_text_transforms=["filter_markdown"],
    )

    @session.on("user_input_transcribed")
    def _on_user_transcribed(ev) -> None:
        # Mark end-of-user-speech for response-latency measurement.
        if getattr(ev, "is_final", False):
            _turn_start[0] = time.monotonic()

    @session.on("conversation_item_added")
    def _on_item(ev) -> None:
        item = ev.item
        role = getattr(item, "role", None)
        text = getattr(item, "text_content", None)
        if not role or not text:
            return
        if role == "user":
            _turn_start[0] = time.monotonic()
            asyncio.create_task(_record_turn("user", text, None))
        elif role == "assistant":
            latency_ms = None
            if _turn_start[0] is not None:
                latency_ms = int((time.monotonic() - _turn_start[0]) * 1000)
                _turn_start[0] = None
            asyncio.create_task(_record_turn("assistant", text, latency_ms))

    done = asyncio.Event()

    @session.on("close")
    def _on_close(ev) -> None:
        logger.info(f"[livekit] session closed for call {call_id}")
        done.set()

    # --- Connect the bot to the room ---
    room = rtc.Room()

    # --- Recording: capture the caller's audio (left) + bot audio (right) ---
    recorder = _StereoRecorder()
    _rec_tasks: list[asyncio.Task] = []

    async def _consume_user_track(track) -> None:
        try:
            stream = rtc.AudioStream(track, sample_rate=_REC_SR, num_channels=1)
            async for ev in stream:
                recorder.add_user(ev.frame, time.monotonic())
        except Exception as exc:  # pragma: no cover - best effort
            logger.warning(f"[livekit] user audio capture ended: {exc}")

    @room.on("track_subscribed")
    def _on_track_subscribed(track, publication, participant) -> None:
        # The only remote audio track is the SIP caller (the worker participant
        # publishes nothing). Capture it as the user/left channel.
        if track.kind == rtc.TrackKind.KIND_AUDIO:
            logger.info(f"[livekit] recording user track from {participant.identity}")
            _rec_tasks.append(asyncio.create_task(_consume_user_track(track)))

    @room.on("participant_disconnected")
    def _on_disconnect(participant) -> None:
        logger.info(f"[livekit] participant disconnected: {participant.identity}")
        done.set()

    _greeted: list[bool] = [False]
    greeting = "Hi! This is your AI assistant. How can I help you today?"

    def _fire_greeting(reason: str) -> None:
        if _greeted[0]:
            return
        _greeted[0] = True
        logger.info(f"[livekit] firing greeting ({reason})")

        async def _greet() -> None:
            # Cushion so the SIP audio path is established before the first
            # phoneme — otherwise the callee misses the opening words.
            await asyncio.sleep(0.6)
            session.say(greeting, add_to_chat_ctx=False)
            await _record_turn("assistant", greeting, None)

        asyncio.create_task(_greet())

    def _on_attrs_changed(changed: dict, participant) -> None:
        status = changed.get("sip.callStatus") or participant.attributes.get("sip.callStatus")
        logger.info(f"[livekit] attrs changed identity={participant.identity} sip.callStatus={status}")
        if status == "active":
            _fire_greeting(f"sip.callStatus=active for {participant.identity}")

    room.on("participant_attributes_changed", _on_attrs_changed)

    await room.connect(settings.livekit_url, bot_token)
    logger.info(f"[livekit] bot connected to room {room_name}")

    await session.start(
        agent=agent,
        room=room,
        room_input_options=RoomInputOptions(close_on_disconnect=True),
    )

    # Tee the bot's outgoing audio into the recorder (right channel).
    try:
        audio_out = session.output.audio
        if audio_out is not None:
            _orig_capture = audio_out.capture_frame

            async def _tee_capture(frame, _orig=_orig_capture):
                try:
                    recorder.add_bot(frame, time.monotonic())
                except Exception:
                    pass
                return await _orig(frame)

            audio_out.capture_frame = _tee_capture
    except Exception as exc:  # pragma: no cover - best effort
        logger.warning(f"[livekit] could not tee bot audio for recording: {exc}")

    # Inbound: the SIP caller is already present — greet if active.
    for p in room.remote_participants.values():
        if p.attributes.get("sip.callStatus") == "active":
            _fire_greeting(f"existing active SIP participant {p.identity}")
            break

    # Outbound: dial the SIP leg now that the bot is in the room.
    if to_number:
        from voiceagent.telephony.livekit_sip import dial_outbound
        try:
            await dial_outbound(to_number, room_name)
        except Exception as exc:
            logger.error(f"[livekit] outbound SIP dial failed for {room_name}: {exc}")
            done.set()

    try:
        await done.wait()
    finally:
        for t in _rec_tasks:
            t.cancel()
        try:
            await session.aclose()
        except Exception:
            pass
        try:
            await room.disconnect()
        except Exception:
            pass
        # Write the stereo recording (best-effort).
        try:
            out_path = RECORDINGS_DIR / f"{call_id}.wav"
            if recorder.write(out_path):
                logger.info(f"[livekit] recording written: {out_path}")
                if on_recording_saved:
                    await on_recording_saved(f"/recordings/{call_id}.wav")
            else:
                logger.info(f"[livekit] no audio captured for call {call_id} — skipping recording")
        except Exception as exc:
            logger.error(f"[livekit] failed to write recording for call {call_id}: {exc}")
        logger.info(f"[livekit] pipeline finished for call {call_id}")
