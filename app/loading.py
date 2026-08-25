"""The LINE chat loading animation.

`POST /v2/bot/chat/loading/start` shows the typing indicator for a fixed number
of seconds - at most 60, and only in multiples of 5. A slow Claude answer can
outlast that, so `keep_alive` re-issues the call before each window lapses and
the indicator stays up for as long as the work takes.

The first call is made straight off the webhook, before the message is queued,
so the indicator appears even when the user's queue is busy with an earlier
message.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import AsyncIterator

from linebot.v3.messaging import AsyncMessagingApi, ShowLoadingAnimationRequest

logger = logging.getLogger(__name__)

MIN_LOADING_SECONDS = 5
MAX_LOADING_SECONDS = 60


def normalize_loading_seconds(seconds: int) -> int:
    """Clamp to 5..60 and round down to the multiple of 5 the API requires."""
    clamped = max(MIN_LOADING_SECONDS, min(MAX_LOADING_SECONDS, int(seconds)))
    return clamped - (clamped % MIN_LOADING_SECONDS)


class LoadingIndicator:
    def __init__(
        self,
        messaging: AsyncMessagingApi,
        *,
        seconds: int = 20,
        refresh_margin: float = 5.0,
    ) -> None:
        self._messaging = messaging
        self.seconds = normalize_loading_seconds(seconds)
        # Refresh this far before the window lapses so there is no visible gap.
        # The floor only guards against a non-positive interval; `seconds` is
        # already clamped to at least 5, so this cannot become a hot loop.
        self._refresh_after = max(0.05, self.seconds - refresh_margin)

    async def start(self, user_id: str) -> None:
        """Show the indicator once. Never raises - a missing animation is cosmetic."""
        try:
            await self._messaging.show_loading_animation(
                ShowLoadingAnimationRequest(chatId=user_id, loadingSeconds=self.seconds)
            )
        except Exception:
            logger.exception("Could not start the loading animation.")

    @asynccontextmanager
    async def keep_alive(self, user_id: str) -> AsyncIterator[None]:
        """Keep the indicator up for the duration of the block.

        Re-issues the request every `seconds - refresh_margin`, so work that
        runs past the API's 60 second ceiling stays covered.
        """
        task = asyncio.create_task(self._refresh_loop(user_id))
        try:
            yield
        finally:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    async def _refresh_loop(self, user_id: str) -> None:
        while True:
            await asyncio.sleep(self._refresh_after)
            logger.debug("Refreshing the loading animation for %s.", user_id)
            await self.start(user_id)
