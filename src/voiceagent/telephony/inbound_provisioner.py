"""Provision the LiveKit inbound SIP trunk + dispatch rule.

Usage:
    PYTHONPATH=src .venv/bin/python -m voiceagent.telephony.inbound_provisioner

Creates:
  - SIPInboundTrunk that accepts calls to SIP_FROM_NUMBER on LiveKit's SIP edge.
  - SIPDispatchRule (individual) that opens a fresh room per call and dispatches
    the registered "voice-agent" worker to it.

Prints the trunk ID, dispatch rule ID, and the LiveKit SIP URI to point Plivo at.
"""

import asyncio

from livekit import api
from loguru import logger

from voiceagent.config import settings

AGENT_NAME = "voice-agent"
TRUNK_NAME = "voice-agent-inbound"
RULE_NAME = "voice-agent-inbound-rule"
ROOM_PREFIX = "inbound-"


async def provision_inbound() -> None:
    if not all([
        settings.livekit_url,
        settings.livekit_api_key,
        settings.livekit_api_secret,
        settings.sip_from_number,
    ]):
        logger.error("Missing LIVEKIT_* or SIP_FROM_NUMBER in .env")
        return

    lk = api.LiveKitAPI(
        url=settings.livekit_url,
        api_key=settings.livekit_api_key,
        api_secret=settings.livekit_api_secret,
    )
    try:
        trunk = await lk.sip.create_sip_inbound_trunk(
            api.CreateSIPInboundTrunkRequest(
                trunk=api.SIPInboundTrunkInfo(
                    name=TRUNK_NAME,
                    numbers=[settings.sip_from_number],
                )
            )
        )
        print(f"\nInbound Trunk ID: {trunk.sip_trunk_id}")

        rule = await lk.sip.create_sip_dispatch_rule(
            api.CreateSIPDispatchRuleRequest(
                name=RULE_NAME,
                trunk_ids=[trunk.sip_trunk_id],
                rule=api.SIPDispatchRule(
                    dispatch_rule_individual=api.SIPDispatchRuleIndividual(
                        room_prefix=ROOM_PREFIX,
                    )
                ),
                room_config=api.RoomConfiguration(
                    agents=[api.RoomAgentDispatch(agent_name=AGENT_NAME)]
                ),
            )
        )
        print(f"Dispatch Rule ID: {rule.sip_dispatch_rule_id}")

        sip_host = settings.livekit_url.replace("wss://", "").replace("ws://", "").rstrip("/")
        sip_uri = f"sip:{settings.sip_from_number}@{sip_host}"
        print(f"\nPoint Plivo inbound at:  {sip_uri}")
        print(f"(Project SIP host: {sip_host})\n")
    except Exception as exc:
        logger.error(f"Inbound provisioning failed: {exc}")
        raise
    finally:
        await lk.aclose()


if __name__ == "__main__":
    asyncio.run(provision_inbound())
