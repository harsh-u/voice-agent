import asyncio
import time
import wave
from pathlib import Path
from typing import Callable, Any, Awaitable

from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask
from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams
from pipecat.services.deepgram.stt import DeepgramSTTService, LiveOptions
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.frames.frames import TTSSpeakFrame
from pipecat.processors.aggregators.llm_response import LLMFullResponseAggregator

from voiceagent.config import settings


RECORDINGS_DIR = Path("recordings")
RECORDINGS_DIR.mkdir(exist_ok=True)


async def run_pipeline(
    call_id: str,
    room_name: str,
    bot_token: str,
    system_prompt: str,
    voice_id: str,
    llm_model: str,
    on_turn_end: Callable[[str, str, int | None], Any] | None = None,
    on_recording_saved: Callable[[str], Awaitable[None]] | None = None,
) -> None:
    """Build and run a Pipecat voice pipeline connected to a LiveKit room.

    Captures both directions of audio into a stereo WAV (left=user, right=bot)
    via two AudioBufferProcessor instances: one right after transport.input()
    so it sees InputAudioRawFrame before any LLM/aggregator stages, and one
    right after transport.output() that picks up OutputAudioRawFrame.

    Args:
        call_id: Primary key of the Call row — used to name the recording file.
        room_name: The LiveKit room name to join.
        bot_token: JWT token granting the bot access to the room.
        system_prompt: System-level instruction for the LLM.
        voice_id: Cartesia voice ID to use for TTS.
        llm_model: Groq model name to use for LLM inference.
        on_turn_end: Optional async callback invoked after each turn
            with (role, text, latency_ms).
        on_recording_saved: Optional async callback invoked once when the
            recording WAV finishes writing, with the public URL path.
    """
    transport = LiveKitTransport(
        url=settings.livekit_url,
        token=bot_token,
        room_name=room_name,
        params=LiveKitParams(audio_in_enabled=True, audio_out_enabled=True),
    )

    vad = VADProcessor(
        vad_analyzer=SileroVADAnalyzer(params=VADParams(stop_secs=0.2))
    )

    stt = DeepgramSTTService(
        api_key=settings.deepgram_api_key,
        live_options=LiveOptions(
            model=settings.deepgram_model,
            language="en-US",
            smart_format=True,
            interim_results=True,
        ),
    )

    llm = GroqLLMService(
        api_key=settings.groq_api_key,
        model=llm_model or settings.groq_model,
    )

    tts = CartesiaTTSService(
        api_key=settings.cartesia_api_key,
        voice_id=voice_id or settings.cartesia_voice_id,
        model=settings.cartesia_model,
    )

    context = LLMContext()
    context.set_messages([{"role": "system", "content": system_prompt}])
    context_pair = LLMContextAggregatorPair(context)

    transcript_collector = LLMFullResponseAggregator()

    # Two recorders: one for the user side (captures InputAudioRawFrame right
    # off the transport input), one for the bot side (captures
    # OutputAudioRawFrame right after the transport output writes to LiveKit).
    user_recorder = AudioBufferProcessor(num_channels=1)
    bot_recorder = AudioBufferProcessor(num_channels=1)

    pipeline = Pipeline([
        transport.input(),
        user_recorder,
        vad,
        stt,
        context_pair.user(),
        llm,
        transcript_collector,
        tts,
        transport.output(),
        bot_recorder,
        context_pair.assistant(),
    ])

    task = PipelineTask(pipeline)

    # Track when the user stopped speaking to measure response latency
    _turn_start_time: list[float | None] = [None]
    _greeted: list[bool] = [False]
    _user_audio: list[tuple[bytes, int] | None] = [None]  # (audio, sample_rate)
    _bot_audio: list[tuple[bytes, int] | None] = [None]

    async def _fire_greeting(reason: str) -> None:
        if _greeted[0]:
            return
        _greeted[0] = True
        logger.info(f"Firing greeting ({reason})")
        # Small cushion so the first phoneme isn't clipped by carrier jitter.
        await asyncio.sleep(0.3)
        await user_recorder.start_recording()
        await bot_recorder.start_recording()
        await task.queue_frames([
            TTSSpeakFrame("Hi! This is your AI assistant. How can I help you today?")
        ])

    @transport.event_handler("on_connected")
    async def on_connected(transport: LiveKitTransport):
        # Hook participant_attributes_changed on the underlying rtc.Room so we
        # know when the SIP leg actually answers (sip.callStatus = "active").
        # For inbound calls the SIP participant is already active when it
        # joins, so we also check existing participants below.
        room = transport._client.room

        def _on_attrs_changed(changed: dict, participant) -> None:
            status = changed.get("sip.callStatus") or participant.attributes.get("sip.callStatus")
            logger.info(
                f"participant_attributes_changed identity={participant.identity} "
                f"sip.callStatus={status} changed={changed}"
            )
            if status == "active":
                asyncio.create_task(_fire_greeting(f"sip.callStatus=active for {participant.identity}"))

        room.on("participant_attributes_changed", _on_attrs_changed)

        for p in room.remote_participants.values():
            status = p.attributes.get("sip.callStatus")
            logger.info(f"on_connected: existing participant {p.identity} sip.callStatus={status}")
            if status == "active":
                await _fire_greeting(f"existing active SIP participant {p.identity}")
                break

    @transport.event_handler("on_audio_track_subscribed")
    async def on_audio_track_subscribed(transport: LiveKitTransport, participant_id: str):
        room = transport._client.room
        for p in room.remote_participants.values():
            if p.sid == participant_id:
                status = p.attributes.get("sip.callStatus")
                logger.info(
                    f"on_audio_track_subscribed identity={p.identity} sip.callStatus={status}"
                )
                if status and status != "active":
                    return
                break
        await _fire_greeting(f"audio track subscribed for {participant_id}")

    @transport.event_handler("on_participant_disconnected")
    async def on_participant_disconnected(transport: LiveKitTransport, participant_id: str):
        logger.info(f"Participant disconnected: {participant_id} — cancelling pipeline task")
        await task.cancel()

    @transcript_collector.event_handler("on_completion")
    async def on_assistant_completion(
        aggregator: LLMFullResponseAggregator,
        completion: str,
        completed: bool,
    ):
        if completed and on_turn_end and completion.strip():
            latency_ms: int | None = None
            if _turn_start_time[0] is not None:
                latency_ms = int((time.monotonic() - _turn_start_time[0]) * 1000)
                _turn_start_time[0] = None
            await on_turn_end("assistant", completion, latency_ms)
            logger.info(f"Assistant turn (latency={latency_ms}ms): {completion[:120]}")

    # Capture user transcript turns when the user-aggregator finalizes them
    # (fires after STT produces a final transcription + VAD detects end-of-turn).
    user_aggregator = context_pair.user()

    @user_aggregator.event_handler("on_user_turn_started")
    async def on_user_turn_started(aggregator, strategy):
        _turn_start_time[0] = time.monotonic()
        logger.info(f"User turn started (strategy={type(strategy).__name__})")

    @user_aggregator.event_handler("on_user_turn_stopped")
    async def on_user_turn_stopped(aggregator, strategy, message):
        # `message` is the assembled user-role chat message dict, e.g.
        # {"role": "user", "content": "hello there"}.
        content = None
        if isinstance(message, dict):
            content = message.get("content")
        else:
            content = getattr(message, "content", None)
        if isinstance(content, list):
            # Multi-part content (e.g. text + image) — pull the text bits.
            content = " ".join(
                p.get("text", "") for p in content if isinstance(p, dict) and p.get("type") == "text"
            )
        if content and isinstance(content, str) and content.strip() and on_turn_end:
            await on_turn_end("user", content.strip(), None)
            logger.info(f"User turn: {content.strip()[:120]}")

    @user_recorder.event_handler("on_audio_data")
    async def on_user_audio(buffer, audio: bytes, sample_rate: int, num_channels: int):
        # Returns the merged buffer. Since we only feed it InputAudioRawFrame
        # (it never sees OutputAudioRawFrame at this position) the "merge"
        # is effectively just the user channel.
        _user_audio[0] = (audio, sample_rate)
        logger.info(f"user_recorder captured: {len(audio)} bytes @ {sample_rate}Hz")

    @bot_recorder.event_handler("on_audio_data")
    async def on_bot_audio(buffer, audio: bytes, sample_rate: int, num_channels: int):
        _bot_audio[0] = (audio, sample_rate)
        logger.info(f"bot_recorder captured: {len(audio)} bytes @ {sample_rate}Hz")

    runner = PipelineRunner()
    try:
        await runner.run(task)
    finally:
        for rec in (user_recorder, bot_recorder):
            try:
                await rec.stop_recording()
            except Exception as exc:
                logger.warning(f"stop_recording failed: {exc}")

        # Write a stereo WAV: left channel = user, right channel = bot.
        # Pad the shorter side with silence so both align.
        out_path = RECORDINGS_DIR / f"{call_id}.wav"
        user_audio, user_sr = _user_audio[0] or (b"", 0)
        bot_audio, bot_sr = _bot_audio[0] or (b"", 0)

        if not user_audio and not bot_audio:
            logger.info(f"No audio captured for call {call_id} — skipping recording")
        else:
            sample_rate = user_sr or bot_sr or 16000
            target_len = max(len(user_audio), len(bot_audio))
            user_audio = user_audio + b"\x00" * (target_len - len(user_audio))
            bot_audio = bot_audio + b"\x00" * (target_len - len(bot_audio))
            # Interleave 16-bit samples: L, R, L, R, ...
            interleaved = bytearray()
            for i in range(0, target_len, 2):
                interleaved += user_audio[i:i + 2]
                interleaved += bot_audio[i:i + 2]
            try:
                with wave.open(str(out_path), "wb") as wf:
                    wf.setnchannels(2)
                    wf.setsampwidth(2)
                    wf.setframerate(sample_rate)
                    wf.writeframes(bytes(interleaved))
                relative_url = f"/recordings/{call_id}.wav"
                logger.info(
                    f"Recording written: {out_path} "
                    f"(user={len(user_audio)} bot={len(bot_audio)} bytes "
                    f"@ {sample_rate}Hz stereo) → {relative_url}"
                )
                if on_recording_saved:
                    await on_recording_saved(relative_url)
            except Exception as exc:
                logger.error(f"Failed to write recording {out_path}: {exc}")
