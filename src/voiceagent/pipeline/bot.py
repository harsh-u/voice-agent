import asyncio
import time
from datetime import datetime, UTC
from typing import Callable, Any

from loguru import logger

from pipecat.pipeline.pipeline import Pipeline
from pipecat.pipeline.task import PipelineTask, PipelineParams
from pipecat.pipeline.runner import PipelineRunner
from pipecat.transports.livekit.transport import LiveKitTransport, LiveKitParams
from pipecat.services.deepgram.stt import DeepgramSTTService, LiveOptions
from pipecat.services.groq.llm import GroqLLMService
from pipecat.services.cartesia.tts import CartesiaTTSService
from pipecat.audio.vad.silero import SileroVADAnalyzer
from pipecat.audio.vad.vad_analyzer import VADParams
from pipecat.processors.audio.vad_processor import VADProcessor
from pipecat.processors.aggregators.llm_response_universal import LLMContextAggregatorPair
from pipecat.processors.aggregators.llm_context import LLMContext
from pipecat.frames.frames import (
    UserStoppedSpeakingFrame,
    BotStartedSpeakingFrame,
    BotStoppedSpeakingFrame,
    TranscriptionFrame,
    TTSSpeakFrame,
)
from pipecat.processors.aggregators.llm_response import LLMFullResponseAggregator

from voiceagent.config import settings


async def run_pipeline(
    room_name: str,
    bot_token: str,
    system_prompt: str,
    voice_id: str,
    llm_model: str,
    on_turn_end: Callable[[str, str, int | None], Any] | None = None,
) -> None:
    """Build and run a Pipecat voice pipeline connected to a LiveKit room.

    Args:
        room_name: The LiveKit room name to join.
        bot_token: JWT token granting the bot access to the room.
        system_prompt: System-level instruction for the LLM.
        voice_id: Cartesia voice ID to use for TTS.
        llm_model: Groq model name to use for LLM inference.
        on_turn_end: Optional async callback invoked after each assistant turn
            with (role, text, latency_ms).
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

    pipeline = Pipeline([
        transport.input(),
        vad,
        stt,
        context_pair.user(),
        llm,
        transcript_collector,
        tts,
        transport.output(),
        context_pair.assistant(),
    ])

    task = PipelineTask(pipeline)

    # Track when the user stopped speaking to measure response latency
    _turn_start_time: list[float | None] = [None]

    @transport.event_handler("on_first_participant_joined")
    async def on_first_participant_joined(transport: LiveKitTransport, participant_id: str):
        logger.info(f"First participant joined: {participant_id} — waiting for media path, then greeting")
        await asyncio.sleep(1.5)
        await task.queue_frames([
            TTSSpeakFrame("Hi! This is your AI assistant. How can I help you today?")
        ])

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
            logger.debug(f"Assistant turn completed (latency={latency_ms}ms): {completion[:80]}")

    runner = PipelineRunner()
    await runner.run(task)
