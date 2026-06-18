"""Wire-format Pydantic models for the SDK → API ingest contract (§2.4)."""
from __future__ import annotations
from datetime import datetime
from typing import Annotated, Literal, Optional, Union
from pydantic import BaseModel, Field, model_validator


# ---------------------------------------------------------------------------
# Per-component fields payloads (§2.3)
# ---------------------------------------------------------------------------

class VadFields(BaseModel):
    component: Literal["vad"] = "vad"
    speech_start_ms: Optional[float] = None
    speech_stop_ms: Optional[float] = None
    stop_secs_config: Optional[float] = None
    false_trigger: bool = False


class SttFields(BaseModel):
    component: Literal["stt"] = "stt"
    provider: Optional[str] = None
    model: Optional[str] = None
    language: Optional[str] = None
    partial_count: int = 0
    final_transcript: Optional[str] = None
    audio_seconds: Optional[float] = None
    wer_proxy: Optional[float] = None
    ttfw_ms: Optional[float] = None  # time-to-first-word


class LlmFields(BaseModel):
    component: Literal["llm"] = "llm"
    provider: Optional[str] = None
    model: Optional[str] = None
    tokens_in: Optional[int] = None
    tokens_out: Optional[int] = None
    ttft_ms: Optional[float] = None  # time-to-first-token
    temperature: Optional[float] = None
    cost_cents: Optional[float] = None


class TtsFields(BaseModel):
    component: Literal["tts"] = "tts"
    provider: Optional[str] = None
    model: Optional[str] = None
    voice_id: Optional[str] = None
    chars: Optional[int] = None
    ttfb_ms: Optional[float] = None
    audio_seconds: Optional[float] = None
    cost_cents: Optional[float] = None


class TransportFields(BaseModel):
    component: Literal["transport"] = "transport"
    kind: Literal["webrtc"] = "webrtc"
    jitter_ms: Optional[float] = None
    packet_loss_pct: Optional[float] = None
    rtt_ms: Optional[float] = None
    codec: Optional[str] = None


class TelephonyFields(BaseModel):
    component: Literal["telephony"] = "telephony"
    kind: Literal["sip"] = "sip"
    carrier: Optional[str] = None
    minutes: Optional[float] = None
    sip_code: Optional[int] = None
    direction: Optional[str] = None  # "inbound" | "outbound"
    cost_cents: Optional[float] = None


# Discriminated union keyed on "component"
ComponentFields = Annotated[
    Union[VadFields, SttFields, LlmFields, TtsFields, TransportFields, TelephonyFields],
    Field(discriminator="component"),
]

# Map component value -> its typed fields model, for enforcement at ingest time.
_COMPONENT_FIELD_MODELS: dict[str, type[BaseModel]] = {
    "vad": VadFields,
    "stt": SttFields,
    "llm": LlmFields,
    "tts": TtsFields,
    "transport": TransportFields,
    "telephony": TelephonyFields,
}


# ---------------------------------------------------------------------------
# Wire-format span / turn / trace envelopes
# ---------------------------------------------------------------------------

class IngestSpan(BaseModel):
    span_id: str
    turn_id: str
    trace_id: str
    component: str  # matches ComponentType enum values
    name: str
    start_ms: float
    end_ms: Optional[float] = None
    ttfb_ms: Optional[float] = None
    error: Optional[str] = None
    fields: Optional[dict] = None  # validated against the per-component typed model below

    @model_validator(mode="after")
    def _enforce_component_fields(self) -> "IngestSpan":
        """Enforce the per-component typed field set (§2.3 "separate components data").

        When `fields` is provided and `component` is a known component type, validate
        the payload through that component's typed model. Invalid types raise a
        ValidationError (→ 422 at ingest). The normalized payload is written back so
        downstream storage holds only recognized, typed keys. Unknown components are
        left untouched (the DB enum rejects them at write time).
        """
        if self.fields is not None:
            model_cls = _COMPONENT_FIELD_MODELS.get(self.component)
            if model_cls is not None:
                payload = dict(self.fields)
                payload.setdefault("component", self.component)
                validated = model_cls.model_validate(payload)
                # Drop the redundant discriminator key from stored fields (matches §2.4 wire shape).
                self.fields = validated.model_dump(exclude={"component"}, exclude_none=True)
        return self


class IngestTurn(BaseModel):
    turn_id: str
    turn_index: int = 0
    role: str = "agent"
    user_transcript: Optional[str] = None
    agent_transcript: Optional[str] = None
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    interrupted: bool = False


class IngestTrace(BaseModel):
    trace_id: str
    external_call_id: Optional[str] = None
    framework: str = "custom"
    started_at: Optional[datetime] = None
    ended_at: Optional[datetime] = None
    status: str = "active"
    meta: Optional[dict] = None


class IngestBatch(BaseModel):
    sdk_version: str = "0.1.0"
    trace: IngestTrace
    turns: list[IngestTurn] = Field(default_factory=list)
    spans: list[IngestSpan] = Field(default_factory=list)


class IngestResponse(BaseModel):
    accepted: int
