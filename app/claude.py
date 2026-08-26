"""Claude calls: replies, document summaries, image descriptions, history compaction.

The SDK's own retry loop is switched off (`max_retries=0`) so `app.retry` is the
only thing retrying. That matters because the policy has to distinguish a 429
(wait exactly as long as `retry-after` says, and warn the user if that is a
long time) from a 529 (back off exponentially on our own schedule).
"""

from __future__ import annotations

import base64
import logging
from collections.abc import Awaitable, Callable, Iterable
from typing import Any

import anthropic

from app.config import Settings
from app.context import truncate
from app.retry import RetriesExhausted, RetryPolicy, call_with_retries
from app.store import Turn

logger = logging.getLogger(__name__)

_FALLBACK_BETA = "server-side-fallback-2026-07-01"

REFUSAL_REPLY = "ごめんなさい、その内容にはお答えできませんでした。"
ERROR_REPLY = "すみません、いまうまく応答できませんでした。少し時間をおいて試してください。"
BUSY_REPLY = "混み合っているようで、応答を取得できませんでした。もう一度送ってみてください。"

# Notify callback: given the whole-second wait, tell the user how long it will be.
Notifier = Callable[[int], Awaitable[None]]

_DOC_SUMMARY_PROMPT = (
    "次の資料の要約を作ってください。あとで「この資料が今の話題に関係あるか」を"
    "判断するための索引として使います。\n"
    "- 何についての資料か、扱っている固有名詞（人名・組織名・製品名・日付・金額）を必ず含める\n"
    "- 400字程度\n"
    "- 前置きは書かず、要約本文だけを返す"
)

_IMAGE_SUMMARY_PROMPT = (
    "この画像の内容を説明してください。あとで検索の索引として使います。\n"
    "- 写っているもの、読み取れる文字はすべて書き出す\n"
    "- 400字程度\n"
    "- 前置きは書かず、説明本文だけを返す"
)

_COMPACT_PROMPT = (
    "次の会話履歴を要約してください。以降のやり取りで参照する記憶になります。\n"
    "- 決まったこと、ユーザーの好みや事情、未解決の論点を残す\n"
    "- 相づちや繰り返しは落とす\n"
    "- 600字程度\n"
    "- 前置きは書かず、要約本文だけを返す"
)


class ClaudeClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._client = anthropic.AsyncAnthropic(
            api_key=settings.anthropic_api_key,
            # app.retry owns retrying; see the module docstring.
            max_retries=0,
        )
        self._policy = RetryPolicy(
            notify_threshold_seconds=settings.notify_wait_threshold_seconds
        )
        # Flipped off permanently if the account cannot use the fallback beta.
        self._use_fallback = settings.refusal_fallback

    async def aclose(self) -> None:
        await self._client.close()

    # -- public operations ----------------------------------------------------

    async def reply(
        self,
        *,
        model: str,
        system: str,
        history: Iterable[Turn],
        user_text: str,
        notify: Notifier | None = None,
    ) -> str:
        """Answer a user message, given the assembled system prompt and history."""
        messages: list[dict[str, Any]] = [
            {"role": turn.role, "content": turn.content} for turn in history
        ]
        messages.append({"role": "user", "content": user_text})
        return await self._text_call(
            model=model,
            system=system,
            messages=messages,
            max_tokens=self._settings.max_tokens,
            effort=self._settings.effort,
            notify=notify,
        )

    async def summarize_document(
        self, *, model: str, filename: str, text: str, notify: Notifier | None = None
    ) -> str:
        content = (
            f"{_DOC_SUMMARY_PROMPT}\n\n"
            f"ファイル名: {filename}\n\n---\n"
            f"{truncate(text, self._settings.summary_input_chars)}"
        )
        return await self._text_call(
            model=model,
            system="あなたは資料を索引化するアシスタントです。",
            messages=[{"role": "user", "content": content}],
            max_tokens=self._settings.summary_max_tokens,
            effort="low",
            notify=notify,
        )

    async def describe_image(
        self,
        *,
        model: str,
        payload: bytes,
        mime: str,
        filename: str,
        notify: Notifier | None = None,
    ) -> str:
        """Summarise an image. The bytes go straight from memory into the request."""
        messages = [
            {
                "role": "user",
                "content": [
                    {
                        "type": "image",
                        "source": {
                            "type": "base64",
                            "media_type": mime,
                            "data": base64.standard_b64encode(payload).decode("ascii"),
                        },
                    },
                    {
                        "type": "text",
                        "text": f"{_IMAGE_SUMMARY_PROMPT}\n\nファイル名: {filename}",
                    },
                ],
            }
        ]
        return await self._text_call(
            model=model,
            system="あなたは画像を索引化するアシスタントです。",
            messages=messages,
            max_tokens=self._settings.summary_max_tokens,
            effort="low",
            notify=notify,
        )

    async def compact_history(
        self,
        *,
        model: str,
        turns: Iterable[Turn],
        previous_summary: str | None = None,
        notify: Notifier | None = None,
    ) -> str:
        transcript = "\n".join(
            f"{'ユーザー' if turn.role == 'user' else 'アシスタント'}: {turn.content}"
            for turn in turns
        )
        parts = [_COMPACT_PROMPT]
        if previous_summary:
            parts.append(f"# これまでの要約\n{previous_summary}")
        trimmed = truncate(transcript, self._settings.summary_input_chars)
        parts.append(f"# 新しいやり取り\n{trimmed}")
        return await self._text_call(
            model=model,
            system="あなたは会話ログを要約するアシスタントです。",
            messages=[{"role": "user", "content": "\n\n".join(parts)}],
            max_tokens=self._settings.summary_max_tokens,
            effort="low",
            notify=notify,
        )

    # -- plumbing -------------------------------------------------------------

    async def _text_call(
        self,
        *,
        model: str,
        system: str,
        messages: list[dict[str, Any]],
        max_tokens: int,
        effort: str,
        notify: Notifier | None,
    ) -> str:
        request: dict[str, Any] = {
            "model": model,
            "max_tokens": max_tokens,
            "system": system,
            "output_config": {"effort": effort},
            "messages": messages,
        }

        try:
            response = await call_with_retries(
                lambda: self._create(request), policy=self._policy, notify=notify
            )
        except RetriesExhausted:
            logger.exception("Gave up calling Claude after retrying.")
            return BUSY_REPLY

        # 仕様書 12.5 フェーズ4検証: /model の切り替えが効いたことをログで確かめる。
        # 要求したモデルと、実際に応答を返したモデルの両方を残す（拒否時のfallbackで
        # 別のモデルが答えることがあるため）。
        logger.info(
            "Claude応答を生成しました。要求モデル=%s 応答モデル=%s stop_reason=%s",
            model,
            getattr(response, "model", "不明"),
            response.stop_reason,
        )

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
        return text

    async def _create(self, request: dict[str, Any]):
        if not self._use_fallback:
            return await self._client.messages.create(**request)
        try:
            return await self._client.beta.messages.create(
                betas=[_FALLBACK_BETA], fallbacks="default", **request
            )
        except anthropic.BadRequestError as exc:
            logger.warning(
                "Refusal fallback rejected by the API; disabling it for this "
                "process and retrying without it: %s",
                exc.message,
            )
            self._use_fallback = False
            return await self._client.messages.create(**request)
