from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # LiveKit
    livekit_url: str = ""
    livekit_api_key: str = ""
    livekit_api_secret: str = ""
    livekit_sip_trunk_id: str = ""

    # STT
    deepgram_api_key: str = ""
    deepgram_model: str = "nova-3"

    # LLM
    groq_api_key: str = ""
    groq_model: str = "llama-3.1-8b-instant"

    # TTS
    cartesia_api_key: str = ""
    cartesia_voice_id: str = "a0e99841-438c-4a64-b679-ae501e7d6091"
    cartesia_model: str = "sonic-2024-10-19"

    # Database
    database_url: str = "postgresql+asyncpg://localhost/voice_agent"

    # SIP
    sip_provider_uri: str = ""
    sip_auth_username: str = ""
    sip_auth_password: str = ""
    sip_from_number: str = ""

    # Cost per minute in cents
    cost_stt_cpm: float = 0.65
    cost_llm_cpm: float = 0.40
    cost_tts_cpm: float = 2.40
    cost_telephony_cpm: float = 0.45

    # Auth (JWT)
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_expire_minutes: int = 1440   # 24 hours
    jwt_refresh_expire_days: int = 30       # 30 days

    # WhatsApp / Meta
    meta_app_secret: str = ""  # for webhook HMAC verification
    # AES-256-GCM key (64 hex chars) for encrypting access tokens at rest
    encryption_key: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
