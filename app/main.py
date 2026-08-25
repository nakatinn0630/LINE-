"""FastAPI app: LINE webhook -> Claude -> reply.

Phase 1 of the personal AI assistant - the minimal round trip. The webhook
verifies the LINE signature, answers 200 immediately, and does the Claude call
plus the reply in a background task, because LINE expects the webhook itself to
return quickly.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import MessageEvent, TextMessageContent

from app.claude import ERROR_REPLY, ClaudeClient
from app.config import get_settings
from app.line_client import LineClient

logging.basicConfig(
    level=os.environ.get("LINE_AI_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    app.state.settings = settings
    app.state.line = LineClient(settings)
    app.state.claude = ClaudeClient(settings)
    logger.info("Ready. model=%s effort=%s", settings.model, settings.effort)
    try:
        yield
    finally:
        await app.state.line.aclose()
        await app.state.claude.aclose()


app = FastAPI(title="LINE Personal AI - Phase 1", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/webhook")
async def webhook(
    request: Request,
    background_tasks: BackgroundTasks,
    x_line_signature: Annotated[str | None, Header()] = None,
) -> dict[str, str]:
    body = (await request.body()).decode("utf-8")

    if not x_line_signature:
        raise HTTPException(status_code=400, detail="Missing X-Line-Signature header")

    try:
        events = request.app.state.line.parser.parse(body, x_line_signature)
    except InvalidSignatureError:
        logger.warning("Rejected a webhook delivery with an invalid signature.")
        raise HTTPException(status_code=400, detail="Invalid signature") from None

    for event in events:
        if isinstance(event, MessageEvent) and isinstance(
            event.message, TextMessageContent
        ):
            background_tasks.add_task(
                handle_text_message,
                request.app,
                event.reply_token,
                event.message.text,
            )
        else:
            logger.info("Ignoring unsupported event: %s", type(event).__name__)

    # LINE only cares that this is a 2xx, and it wants it promptly.
    return {"status": "ok"}


async def handle_text_message(app: FastAPI, reply_token: str, text: str) -> None:
    """Ask Claude, then reply on LINE. Runs after the webhook has returned 200."""
    logger.info("Handling text message (%d chars).", len(text))
    try:
        reply = await app.state.claude.complete(text)
    except Exception:
        logger.exception("Claude call failed.")
        reply = ERROR_REPLY

    try:
        await app.state.line.reply_text(reply_token, reply)
        logger.info("Replied (%d chars).", len(reply))
    except Exception:
        # A reply token is single-use and expires, so there is nothing to retry.
        logger.exception("Failed to send the LINE reply.")
