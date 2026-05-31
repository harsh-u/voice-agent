"""Meta Cloud API client — async httpx wrapper."""
from typing import Any

import httpx
from loguru import logger

GRAPH_URL = "https://graph.facebook.com/v19.0"


class MetaAPI:
    """Thin async client around the Meta Cloud API."""

    def __init__(self, phone_number_id: str, access_token: str):
        self.phone_number_id = phone_number_id
        self._token = access_token
        self._client = httpx.AsyncClient(
            base_url=GRAPH_URL,
            headers={"Authorization": f"Bearer {access_token}"},
            timeout=15.0,
        )

    async def close(self):
        await self._client.aclose()

    # ------------------------------------------------------------------
    # Messaging
    # ------------------------------------------------------------------

    async def send_text(self, to: str, text: str) -> dict:
        return await self._messages({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "text",
            "text": {"body": text, "preview_url": False},
        })

    async def send_template(
        self,
        to: str,
        template_name: str,
        language_code: str = "en_US",
        components: list | None = None,
    ) -> dict:
        body: dict[str, Any] = {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {"name": template_name, "language": {"code": language_code}},
        }
        if components:
            body["template"]["components"] = components
        return await self._messages(body)

    async def send_media(
        self,
        to: str,
        media_type: str,  # image | video | audio | document
        media_url: str | None = None,
        media_id: str | None = None,
        caption: str | None = None,
        filename: str | None = None,
    ) -> dict:
        media: dict = {}
        if media_id:
            media["id"] = media_id
        elif media_url:
            media["link"] = media_url
        if caption:
            media["caption"] = caption
        if filename:
            media["filename"] = filename
        return await self._messages({
            "messaging_product": "whatsapp",
            "to": to,
            "type": media_type,
            media_type: media,
        })

    async def send_interactive(
        self,
        to: str,
        interactive: dict,
    ) -> dict:
        return await self._messages({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "interactive",
            "interactive": interactive,
        })

    async def react(self, to: str, message_id: str, emoji: str) -> dict:
        return await self._messages({
            "messaging_product": "whatsapp",
            "to": to,
            "type": "reaction",
            "reaction": {"message_id": message_id, "emoji": emoji},
        })

    async def mark_read(self, message_id: str) -> dict:
        return await self._messages({
            "messaging_product": "whatsapp",
            "status": "read",
            "message_id": message_id,
        })

    # ------------------------------------------------------------------
    # Templates
    # ------------------------------------------------------------------

    async def create_template(
        self,
        waba_id: str,
        name: str,
        language: str,
        category: str,
        components: list,
    ) -> dict:
        resp = await self._client.post(
            f"/{waba_id}/message_templates",
            json={
                "name": name,
                "language": language,
                "category": category,
                "components": components,
            },
        )
        return self._raise(resp)

    async def delete_template(self, waba_id: str, template_name: str) -> dict:
        resp = await self._client.delete(
            f"/{waba_id}/message_templates",
            params={"name": template_name},
        )
        return self._raise(resp)

    # ------------------------------------------------------------------
    # Media
    # ------------------------------------------------------------------

    async def get_media_url(self, media_id: str) -> str:
        resp = await self._client.get(f"/{media_id}")
        data = self._raise(resp)
        return data["url"]

    async def download_media(self, url: str) -> bytes:
        resp = await self._client.get(url)
        resp.raise_for_status()
        return resp.content

    # ------------------------------------------------------------------
    # Phone / webhook registration
    # ------------------------------------------------------------------

    async def register_webhook(
        self,
        waba_id: str,
        callback_url: str,
        verify_token: str,
    ) -> dict:
        resp = await self._client.post(
            f"/{waba_id}/subscribed_apps",
            json={
                "callback_url": callback_url,
                "verify_token": verify_token,
                "object": "whatsapp_business_account",
            },
        )
        return self._raise(resp)

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    async def _messages(self, payload: dict) -> dict:
        resp = await self._client.post(
            f"/{self.phone_number_id}/messages",
            json=payload,
        )
        return self._raise(resp)

    @staticmethod
    def _raise(resp: httpx.Response) -> dict:
        try:
            resp.raise_for_status()
            return resp.json()
        except httpx.HTTPStatusError as e:
            logger.error(f"Meta API error {resp.status_code}: {resp.text}")
            raise
