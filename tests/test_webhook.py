"""End-to-end tests for the webhook, with Claude and the LINE reply stubbed out."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json

import pytest
from fastapi.testclient import TestClient

CHANNEL_SECRET = "test-channel-secret"


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("LINE_CHANNEL_SECRET", CHANNEL_SECRET)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")

    from app.config import get_settings

    get_settings.cache_clear()

    from app import main

    sent: list[tuple[str, str]] = []

    async def fake_complete(self, user_text: str) -> str:
        return f"echo: {user_text}"

    async def fake_reply_text(self, reply_token: str, text: str) -> None:
        sent.append((reply_token, text))

    monkeypatch.setattr(main.ClaudeClient, "complete", fake_complete)
    monkeypatch.setattr(main.LineClient, "reply_text", fake_reply_text)

    with TestClient(main.app) as test_client:
        test_client.sent = sent
        yield test_client

    get_settings.cache_clear()


def sign(body: str) -> str:
    digest = hmac.new(
        CHANNEL_SECRET.encode("utf-8"), body.encode("utf-8"), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode("utf-8")


def text_message_body(text: str = "こんにちは") -> str:
    return json.dumps(
        {
            "destination": "Uxxxxxxxxxxxxxx",
            "events": [
                {
                    "type": "message",
                    "mode": "active",
                    "timestamp": 1700000000000,
                    "replyToken": "reply-token-1",
                    "webhookEventId": "01FZ",
                    "deliveryContext": {"isRedelivery": False},
                    "source": {"type": "user", "userId": "Uxxxxxxxxxxxxxx"},
                    "message": {
                        "id": "1",
                        "type": "text",
                        "text": text,
                        "quoteToken": "quote-token-1",
                    },
                }
            ],
        }
    )


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_valid_signature_triggers_reply(client):
    body = text_message_body()
    response = client.post(
        "/webhook", content=body, headers={"X-Line-Signature": sign(body)}
    )
    assert response.status_code == 200
    assert client.sent == [("reply-token-1", "echo: こんにちは")]


def test_invalid_signature_is_rejected(client):
    body = text_message_body()
    response = client.post(
        "/webhook", content=body, headers={"X-Line-Signature": "not-a-signature"}
    )
    assert response.status_code == 400
    assert client.sent == []


def test_missing_signature_header_is_rejected(client):
    response = client.post("/webhook", content=text_message_body())
    assert response.status_code == 400
    assert client.sent == []


def test_non_text_event_is_ignored(client):
    body = json.dumps(
        {
            "destination": "Uxxxxxxxxxxxxxx",
            "events": [
                {
                    "type": "message",
                    "mode": "active",
                    "timestamp": 1700000000000,
                    "replyToken": "reply-token-2",
                    "webhookEventId": "01FZ",
                    "deliveryContext": {"isRedelivery": False},
                    "source": {"type": "user", "userId": "Uxxxxxxxxxxxxxx"},
                    "message": {"id": "2", "type": "sticker",
                                "stickerId": "1", "packageId": "1",
                                "stickerResourceType": "STATIC"},
                }
            ],
        }
    )
    response = client.post(
        "/webhook", content=body, headers={"X-Line-Signature": sign(body)}
    )
    assert response.status_code == 200
    assert client.sent == []
