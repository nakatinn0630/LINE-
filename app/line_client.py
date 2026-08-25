"""LINE Messaging API helpers."""

from __future__ import annotations

import logging

from linebot.v3 import WebhookParser
from linebot.v3.messaging import (
    AsyncApiClient,
    AsyncMessagingApi,
    Configuration,
    ReplyMessageRequest,
    TextMessage,
)

from app.config import Settings

logger = logging.getLogger(__name__)


class LineClient:
    def __init__(self, settings: Settings) -> None:
        self.parser = WebhookParser(settings.line_channel_secret)
        self._api_client = AsyncApiClient(
            Configuration(access_token=settings.line_channel_access_token)
        )
        self._messaging = AsyncMessagingApi(self._api_client)

    async def aclose(self) -> None:
        await self._api_client.close()

    async def reply_text(self, reply_token: str, text: str) -> None:
        await self._messaging.reply_message(
            ReplyMessageRequest(
                replyToken=reply_token,
                messages=[TextMessage(text=text)],
            )
        )
