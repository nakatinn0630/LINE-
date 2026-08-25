"""The message pipeline: context in, Claude, encrypted state out.

Text messages get the rolling summary, the recent turns and any relevant
documents assembled into a prompt. File and image messages get downloaded into
memory, parsed, summarised, and stored encrypted - the body, the extracted
text and the summary all sealed with the user's derived key.

Anything that touches sqlite or parses a document runs through
`asyncio.to_thread`, so a slow PDF cannot stall the event loop while other
users are waiting.
"""

from __future__ import annotations

import asyncio
import logging
import time

from app.claude import ClaudeClient
from app.commands import MODEL_SETTING_KEY
from app.config import Settings
from app.context import build_system_prompt, select_documents, truncate
from app.crypto import Crypto
from app.extract import ExtractionError, extract_text, guess_mime, SUPPORTED_IMAGE_MIMES
from app.line_client import ContentTooLargeError, LineClient
from app.store import UserStore
from app.vault import Vault
from app import commands

logger = logging.getLogger(__name__)

TOO_LARGE_REPLY = "ファイルが大きすぎて受け取れませんでした。"
UNREADABLE_REPLY = "ファイルを受け取りましたが、中身を読み取れませんでした。暗号化して保存だけしておきます。"
DOWNLOAD_FAILED_REPLY = "ファイルを受け取れませんでした。もう一度送ってみてください。"


class Assistant:
    def __init__(
        self, settings: Settings, line: LineClient, claude: ClaudeClient
    ) -> None:
        self._settings = settings
        self._line = line
        self._claude = claude
        self._crypto = Crypto(settings.master_key)
        self._vault = Vault(settings.data_dir, self._crypto)
        self._stores: dict[str, UserStore] = {}

    def store_for(self, user_id: str) -> UserStore:
        store = self._stores.get(user_id)
        if store is None:
            store = UserStore(
                self._vault.user_dir(user_id) / "store.db", user_id, self._crypto
            )
            self._stores[user_id] = store
        return store

    def _notifier(self, user_id: str):
        """Callback the retry policy uses to warn about a long rate-limit wait."""

        async def notify(seconds: int) -> None:
            await self._line.push_text(user_id, f"{seconds}秒後に返答します。")

        return notify

    # -- text -----------------------------------------------------------------

    async def handle_text(self, user_id: str, reply_token: str, text: str) -> None:
        store = await asyncio.to_thread(self.store_for, user_id)

        if commands.is_command(text):
            reply = await asyncio.to_thread(
                commands.handle, text, settings=self._settings, store=store
            )
            await self._line.reply_text(reply_token, reply)
            return

        model = await asyncio.to_thread(self._model_for, store)
        system, history = await asyncio.to_thread(self._build_context, user_id, store, text)

        reply = await self._claude.reply(
            model=model,
            system=system,
            history=history,
            user_text=text,
            notify=self._notifier(user_id),
        )
        reply = self._clip(reply)

        await asyncio.to_thread(self._record_exchange, store, text, reply)
        await self._line.reply_text(reply_token, reply)
        await self._maybe_compact(user_id, store, model)

    def _build_context(self, user_id: str, store: UserStore, text: str):
        """Assemble the system prompt and history. Runs off the event loop."""
        summary = store.latest_summary()
        history = store.recent_turns(self._settings.history_exchanges)

        selected = select_documents(
            text,
            store.documents(),
            limit=self._settings.document_limit,
            threshold=self._settings.document_threshold,
        )
        documents: list[tuple[str, str]] = []
        for hit in selected:
            body = self._document_text(user_id, hit.entry)
            if body:
                documents.append(
                    (hit.entry.filename, truncate(body, self._settings.document_inject_chars))
                )
                logger.info(
                    "Injecting %s (relevance %.2f).", hit.entry.filename, hit.score
                )

        system = build_system_prompt(
            self._settings.system_prompt,
            conversation_summary=summary[1] if summary else None,
            documents=documents,
        )
        return system, history

    def _document_text(self, user_id: str, entry) -> str:
        """Decrypt a document's text into memory. Falls back to its summary."""
        if entry.text_record_id:
            try:
                return self._vault.read_text(user_id, entry.text_record_id)
            except (OSError, ValueError) as exc:
                logger.warning(
                    "Could not read the text of %s: %s", entry.record_id, exc
                )
        return entry.summary

    @staticmethod
    def _record_exchange(store: UserStore, user_text: str, reply: str) -> None:
        store.add_turn("user", user_text)
        store.add_turn("assistant", reply)

    def _model_for(self, store: UserStore) -> str:
        return self._settings.resolve_model(store.get_setting(MODEL_SETTING_KEY))

    # -- files and images -----------------------------------------------------

    async def handle_media(
        self,
        user_id: str,
        reply_token: str,
        *,
        message_id: str,
        filename: str,
        kind: str,
    ) -> None:
        store = await asyncio.to_thread(self.store_for, user_id)
        model = await asyncio.to_thread(self._model_for, store)

        try:
            payload = await self._line.get_message_content(message_id)
        except ContentTooLargeError as exc:
            logger.warning("Refused an oversized upload: %s", exc)
            await self._line.reply_text(reply_token, TOO_LARGE_REPLY)
            return
        except Exception:
            logger.exception("Failed to download message content.")
            await self._line.reply_text(reply_token, DOWNLOAD_FAILED_REPLY)
            return

        mime = guess_mime(filename, payload)
        created_at = time.time()

        text: str | None = None
        extraction_error: str | None = None
        if kind == "image" or mime in SUPPORTED_IMAGE_MIMES:
            summary = await self._claude.describe_image(
                model=model,
                payload=payload,
                mime=mime if mime in SUPPORTED_IMAGE_MIMES else "image/jpeg",
                filename=filename,
                notify=self._notifier(user_id),
            )
        else:
            try:
                text = await asyncio.to_thread(
                    extract_text, payload, mime=mime, filename=filename
                )
            except ExtractionError as exc:
                extraction_error = str(exc)
                logger.warning("Extraction failed for %s: %s", filename, exc)

            if text:
                summary = await self._claude.summarize_document(
                    model=model,
                    filename=filename,
                    text=text,
                    notify=self._notifier(user_id),
                )
            else:
                summary = (
                    f"{filename}（{mime}）。"
                    + (f"読み取りに失敗: {extraction_error}" if extraction_error
                       else "テキストを抽出できない形式です。")
                )

        record_id, text_record_id = await asyncio.to_thread(
            self._persist,
            user_id,
            store,
            payload=payload,
            text=text,
            summary=summary,
            filename=filename,
            mime=mime,
            kind=kind,
            created_at=created_at,
        )
        logger.info(
            "Stored %s as %s (text record %s).", filename, record_id, text_record_id
        )

        reply = (
            UNREADABLE_REPLY
            if text is None and kind != "image"
            else self._clip(f"「{filename}」を保存しました。\n\n{summary}")
        )
        await asyncio.to_thread(
            self._record_exchange, store, f"[ファイルを共有: {filename}]", reply
        )
        await self._line.reply_text(reply_token, reply)

    def _persist(
        self,
        user_id: str,
        store: UserStore,
        *,
        payload: bytes,
        text: str | None,
        summary: str,
        filename: str,
        mime: str,
        kind: str,
        created_at: float,
    ) -> tuple[str, str | None]:
        """Encrypt and store the body, the extracted text and the summary."""
        record_id = self._vault.write(
            user_id,
            payload,
            filename=filename,
            mime=mime,
            kind=kind,
            created_at=created_at,
        )
        text_record_id = None
        if text:
            text_record_id = self._vault.write(
                user_id,
                text.encode("utf-8"),
                filename=f"{filename}.txt",
                mime="text/plain",
                kind="extracted-text",
                created_at=created_at,
            )
        store.add_document(
            record_id,
            kind=kind,
            filename=filename,
            summary=summary,
            text_record_id=text_record_id,
            created_at=created_at,
        )
        return record_id, text_record_id

    # -- history compaction ---------------------------------------------------

    async def _maybe_compact(self, user_id: str, store: UserStore, model: str) -> None:
        """Fold older turns into the rolling summary once history gets long."""
        plan = await asyncio.to_thread(self._compaction_plan, store)
        if plan is None:
            return
        cutoff, turns, previous = plan

        logger.info("Compacting %d turns up to message %d.", len(turns), cutoff)
        try:
            summary = await self._claude.compact_history(
                model=model,
                turns=turns,
                previous_summary=previous,
                notify=self._notifier(user_id),
            )
        except Exception:
            # Compaction is housekeeping; failing it must not affect the reply
            # the user already received.
            logger.exception("History compaction failed; will retry next turn.")
            return
        await asyncio.to_thread(store.set_summary, cutoff, summary)

    def _compaction_plan(self, store: UserStore):
        if store.message_count() <= self._settings.compact_after_messages:
            return None
        keep = self._settings.history_exchanges * 2
        cutoff = store.latest_message_id() - keep
        if cutoff <= 0:
            return None

        previous = store.latest_summary()
        previous_upto = previous[0] if previous else 0
        if previous_upto >= cutoff:
            return None

        turns = store.turns_between(previous_upto, cutoff)
        if not turns:
            return None
        return cutoff, turns, (previous[1] if previous else None)

    def _clip(self, text: str) -> str:
        limit = self._settings.max_reply_chars
        return text if len(text) <= limit else text[: limit - 1] + "…"
