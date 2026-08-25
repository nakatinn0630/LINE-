"""FastAPI app: LINE webhook -> Claude -> reply.

The webhook does three things and returns: verify the signature, start the
loading indicator, and hand the work to the user's queue. Everything slow -
downloading a file, parsing it, calling Claude - happens on the queue, with the
loading indicator refreshed underneath it.

The indicator is started before the job is queued, so it appears within a
moment of the message arriving even when the user's queue is still busy with an
earlier message.
"""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Annotated, AsyncIterator

from fastapi import BackgroundTasks, FastAPI, Header, HTTPException, Request
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    FileMessageContent,
    ImageMessageContent,
    MessageEvent,
    TextMessageContent,
)

from app.assistant import Assistant
from app.claude import ERROR_REPLY, ClaudeClient
from app.config import get_settings
from app.line_client import LineClient
from app.loading import LoadingIndicator
from app.queues import UserQueues

logging.basicConfig(
    level=os.environ.get("LINE_AI_LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)-8s %(name)s: %(message)s",
)
logger = logging.getLogger(__name__)

BUSY_QUEUE_REPLY = "いま前のメッセージを処理中です。少し待ってから送ってください。"


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    line = LineClient(settings)
    claude = ClaudeClient(settings)

    app.state.settings = settings
    app.state.line = line
    app.state.claude = claude
    app.state.assistant = Assistant(settings, line, claude)
    app.state.loading = LoadingIndicator(
        line.messaging, seconds=settings.loading_seconds
    )
    app.state.queues = UserQueues(max_pending=settings.queue_max_pending)

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    logger.info(
        "Ready. model=%s effort=%s data=%s", settings.model, settings.effort, settings.data_dir
    )
    try:
        yield
    finally:
        await app.state.queues.aclose()
        await line.aclose()
        await claude.aclose()


app = FastAPI(title="LINE Personal AI", lifespan=lifespan)


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
        _schedule(request.app, background_tasks, event)

    # LINE only cares that this is a 2xx, and it wants it promptly.
    return {"status": "ok"}


def _schedule(app: FastAPI, background_tasks: BackgroundTasks, event) -> None:
    if not isinstance(event, MessageEvent):
        logger.info("Ignoring unsupported event: %s", type(event).__name__)
        return

    user_id = getattr(event.source, "user_id", None)
    if not user_id:
        logger.info("Ignoring an event with no user id (source=%s).", event.source.type)
        return

    message = event.message
    assistant: Assistant = app.state.assistant

    if isinstance(message, TextMessageContent):
        async def job() -> None:
            await assistant.handle_text(user_id, event.reply_token, message.text)

    elif isinstance(message, (FileMessageContent, ImageMessageContent)):
        filename = getattr(message, "file_name", None) or f"image-{message.id}.jpg"
        kind = "file" if isinstance(message, FileMessageContent) else "image"

        async def job() -> None:
            await assistant.handle_media(
                user_id,
                event.reply_token,
                message_id=message.id,
                filename=filename,
                kind=kind,
            )

    else:
        logger.info("Ignoring unsupported message: %s", type(message).__name__)
        return

    background_tasks.add_task(_dispatch, app, user_id, event.reply_token, job)


async def _dispatch(app: FastAPI, user_id: str, reply_token: str, job) -> None:
    """Show the loading indicator, then queue the work behind it."""
    loading = app.state.loading
    queues: UserQueues = app.state.queues
    line: LineClient = app.state.line

    await loading.start(user_id)

    async def run() -> None:
        async with loading.keep_alive(user_id):
            try:
                await job()
            except Exception:
                logger.exception("Message handling failed.")
                try:
                    await line.reply_text(reply_token, ERROR_REPLY)
                except Exception:
                    # The reply token is single-use and expires; nothing to retry.
                    logger.exception("Could not deliver the failure notice.")

    if not await queues.submit(user_id, run):
        try:
            await line.reply_text(reply_token, BUSY_QUEUE_REPLY)
        except Exception:
            logger.exception("Could not deliver the busy notice.")
