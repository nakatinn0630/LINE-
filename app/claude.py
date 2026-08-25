"""Thin async wrapper around the Anthropic Messages API."""

from __future__ import annotations

import logging

import anthropic

from app.config import Settings

logger = logging.getLogger(__name__)

# Server-side refusal fallback: if a safety classifier declines the request,
# the API re-runs it on a fallback model inside the same call instead of
# leaving the user with no reply at all.
_FALLBACK_BETA = "server-side-fallback-2026-07-01"

REFUSAL_REPLY = "ごめんなさい、その内容にはお答えできませんでした。"
ERROR_REPLY = "すみません、いまうまく応答できませんでした。少し時間をおいて試してください。"


class ClaudeClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = anthropic.AsyncAnthropic(api_key=settings.anthropic_api_key)
        # Flipped off permanently if the account cannot use the fallback beta.
        self._use_fallback = settings.refusal_fallback

    async def aclose(self) -> None:
        await self._client.close()

    async def complete(self, user_text: str) -> str:
        """Send one user message and return the assistant's text reply.

        Phase 1 is deliberately stateless: each LINE message is its own
        conversation. Conversation history lands in a later phase.
        """
        s = self._settings
        common = {
            "model": s.model,
            "max_tokens": s.max_tokens,
            "system": s.system_prompt,
            # Adaptive thinking is on by default for this model family; effort
            # is what actually controls how much of it happens.
            "output_config": {"effort": s.effort},
            "messages": [{"role": "user", "content": user_text}],
        }

        if self._use_fallback:
            try:
                response = await self._client.beta.messages.create(
                    betas=[_FALLBACK_BETA], fallbacks="default", **common
                )
            except anthropic.BadRequestError as exc:
                logger.warning(
                    "Refusal fallback rejected by the API, disabling it for this "
                    "process and retrying without it: %s",
                    exc.message,
                )
                self._use_fallback = False
                response = await self._client.messages.create(**common)
        else:
            response = await self._client.messages.create(**common)

        if response.stop_reason == "refusal":
            category = getattr(response.stop_details, "category", None)
            logger.info("Claude declined the request (category=%s).", category)
            return REFUSAL_REPLY

        text = "".join(
            block.text for block in response.content if block.type == "text"
        ).strip()
        if not text:
            logger.warning(
                "Empty response from Claude (stop_reason=%s).", response.stop_reason
            )
            return ERROR_REPLY

        if len(text) > s.max_reply_chars:
            text = text[: s.max_reply_chars - 1] + "…"
        return text
