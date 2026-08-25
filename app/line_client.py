"""LINE Messaging API helpers."""

from __future__ import annotations

import logging

from linebot.v3 import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    AsyncMessagingApiBlob,
    Configuration,
    PushMessageRequest,
    ReplyMessageRequest,
    TextMessage,
)

logger = logging.getLogger(__name__)


class ContentTooLargeError(RuntimeError):
    """Raised when an uploaded file exceeds the configured size ceiling."""


class LineClient:
    def __init__(self, settings) -> None:
        self.parser = WebhookParser(settings.line_channel_secret)
        self._api_client = AsyncApiClient(
            Configuration(access_token=settings.line_channel_access_token)
        )
        self.messaging = AsyncMessagingApi(self._api_client)
        # The blob client targets https://api-data.line.me, which is where
        # message content is served from.
        self._blob = AsyncMessagingApiBlob(self._api_client)
        self._max_download_bytes = settings.max_download_bytes

    async def aclose(self) -> None:
        await self._api_client.close()

    async def reply_text(self, reply_token: str, text: str) -> None:
        await self.messaging.reply_message(
            ReplyMessageRequest(
                replyToken=reply_token,
                messages=[TextMessage(text=text)],
            )
        )

    async def push_text(self, user_id: str, text: str) -> None:
        await self.messaging.push_message(
            PushMessageRequest(to=user_id, messages=[TextMessage(text=text)])
        )

    async def get_message_content(self, message_id: str) -> bytes:
        """Download message content into memory.

        Hits GET https://api-data.line.me/v2/bot/message/{messageId}/content.
        The bytes are returned to the caller and never written to disk here.
        """
        payload = await self._blob.get_message_content(message_id)
        data = bytes(payload)
        if len(data) > self._max_download_bytes:
            raise ContentTooLargeError(
                f"content is {len(data)} bytes, over the "
                f"{self._max_download_bytes} byte ceiling"
            )
        return data
