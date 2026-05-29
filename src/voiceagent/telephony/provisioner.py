"""SIP trunk provisioner — run once to create the outbound SIP trunk.

Usage:
    uv run python -m voiceagent.telephony.provisioner

This will create an outbound SIP trunk in LiveKit using the credentials defined
in your .env file and print the resulting trunk ID.  Copy that ID and set it as
LIVEKIT_SIP_TRUNK_ID in your .env.
"""

import asyncio

from livekit import api
from loguru import logger

from voiceagent.config import settings


async def provision_sip_trunk() -> None:
    """Create an outbound SIP trunk and print its ID."""
    if not all([
        settings.livekit_url,
        settings.livekit_api_key,
        settings.livekit_api_secret,
        settings.sip_provider_uri,
        settings.sip_auth_username,
        settings.sip_auth_password,
        settings.sip_from_number,
    ]):
        logger.error(
            "Missing required settings. Ensure LIVEKIT_URL, LIVEKIT_API_KEY, "
            "LIVEKIT_API_SECRET, SIP_PROVIDER_URI, SIP_AUTH_USERNAME, "
            "SIP_AUTH_PASSWORD, and SIP_FROM_NUMBER are set in .env"
        )
        return

    lk = api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )

    try:
        trunk = await lk.sip.create_sip_outbound_trunk(
            api.CreateSIPOutboundTrunkRequest(
                trunk=api.SIPOutboundTrunkInfo(
                    name="voice-agent-outbound",
                    address=settings.sip_provider_uri,
                    numbers=[settings.sip_from_number],
                    auth_username=settings.sip_auth_username,
                    auth_password=settings.sip_auth_password,
                )
            )
        )
        logger.success(f"SIP trunk created successfully.")
        print(f"\nTrunk ID: {trunk.sip_trunk_id}")
        print(f"Set this in your .env:  LIVEKIT_SIP_TRUNK_ID={trunk.sip_trunk_id}\n")
    except Exception as exc:
        logger.error(f"Failed to create SIP trunk: {exc}")
        raise
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(provision_sip_trunk())
