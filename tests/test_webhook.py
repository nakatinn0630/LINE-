"""End-to-end tests through the real webhook, with LINE and Claude stubbed.

The signature check, the loading indicator, the queue, the pipeline and the
encrypted stores are all real; only the two network edges are replaced.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import time

import pytest
from fastapi.testclient import TestClient

from tests.conftest import TEST_CHANNEL_SECRET


class FakeLine:
    """Records what would have gone to LINE, and serves fake message content."""

    def __init__(self) -> None:
        self.replies: list[tuple[str, str]] = []
        self.pushes: list[tuple[str, str]] = []
        self.loading: list[tuple[str, float]] = []
        self.content: dict[str, bytes] = {}
        self.started_at = time.monotonic()

    async def reply_text(self, reply_token: str, text: str) -> None:
        self.replies.append((reply_token, text))

    async def push_text(self, user_id: str, text: str) -> None:
        self.pushes.append((user_id, text))

    async def get_message_content(self, message_id: str) -> bytes:
        return self.content[message_id]

    async def show_loading_animation(self, request) -> None:
        self.loading.append((request.chat_id, time.monotonic() - self.started_at))


class FakeClaude:
    def __init__(self) -> None:
        self.replies: list[str] = []
        self.seen_systems: list[str] = []
        self.seen_history: list[list] = []
        self.seen_models: list[str] = []
        self.images: list[bytes] = []

    async def reply(self, *, model, system, history, user_text, notify=None):
        self.seen_systems.append(system)
        self.seen_history.append(list(history))
        self.seen_models.append(model)
        return f"echo:{user_text}"

    async def summarize_document(self, *, model, filename, text, notify=None):
        self.seen_models.append(model)
        return f"要約({filename}): {text[:20]}"

    async def describe_image(self, *, model, payload, mime, filename, notify=None):
        self.images.append(bytes(payload))
        return f"画像({filename}, {mime}, {len(payload)}バイト)"

    async def compact_history(self, *, model, turns, previous_summary=None, notify=None):
        return f"要約{len(list(turns))}件"


@pytest.fixture
def client(secret_env, monkeypatch):
    from app import main

    fake_line = FakeLine()
    fake_claude = FakeClaude()

    real_lifespan = main.lifespan

    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def patched(app):
        async with real_lifespan(app):
            # Swap the two network edges, keeping the real assistant, queues,
            # loading indicator and encrypted stores.
            app.state.assistant._line = fake_line
            app.state.assistant._claude = fake_claude
            app.state.line.reply_text = fake_line.reply_text
            app.state.line.push_text = fake_line.push_text
            app.state.loading._messaging = fake_line
            yield

    monkeypatch.setattr(main, "lifespan", patched)
    main.app.router.lifespan_context = patched

    with TestClient(main.app) as test_client:
        test_client.line = fake_line
        test_client.claude = fake_claude
        test_client.settings = secret_env
        yield test_client


def sign(body: str) -> str:
    digest = hmac.new(
        TEST_CHANNEL_SECRET.encode(), body.encode(), hashlib.sha256
    ).digest()
    return base64.b64encode(digest).decode()


def _event(message: dict, *, reply_token: str, user_id: str) -> str:
    return json.dumps(
        {
            "destination": "Udestination",
            "events": [
                {
                    "type": "message",
                    "mode": "active",
                    "timestamp": 1700000000000,
                    "replyToken": reply_token,
                    "webhookEventId": "01FZ",
                    "deliveryContext": {"isRedelivery": False},
                    "source": {"type": "user", "userId": user_id},
                    "message": message,
                }
            ],
        }
    )


def text_body(text="こんにちは", *, reply_token="rt-1", user_id="Uaaa"):
    return _event(
        {"id": "m1", "type": "text", "text": text, "quoteToken": "q1"},
        reply_token=reply_token,
        user_id=user_id,
    )


def file_body(filename, message_id="m-file", *, reply_token="rt-f", user_id="Uaaa"):
    return _event(
        {"id": message_id, "type": "file", "fileName": filename, "fileSize": 1234},
        reply_token=reply_token,
        user_id=user_id,
    )


def image_body(message_id="m-img", *, reply_token="rt-i", user_id="Uaaa"):
    return _event(
        {
            "id": message_id,
            "type": "image",
            "quoteToken": "q2",
            "contentProvider": {"type": "line"},
        },
        reply_token=reply_token,
        user_id=user_id,
    )


def post(client, body, *, drain=True):
    """POST a signed webhook and, by default, wait for the queued work to finish.

    The webhook returns 200 as soon as the job is queued, so a test that wants
    to see the reply has to wait for the user's queue to drain.
    """
    response = client.post(
        "/webhook", content=body, headers={"X-Line-Signature": sign(body)}
    )
    if drain and response.status_code == 200:
        _drain(client)
    return response


def _drain(client, timeout: float = 10.0) -> None:
    from app.main import app

    deadline = time.monotonic() + timeout
    while not app.state.queues.idle:
        if time.monotonic() > deadline:
            raise AssertionError("queued work did not finish in time")
        time.sleep(0.01)


# -- signature ----------------------------------------------------------------


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_valid_signature_produces_a_reply(client):
    assert post(client, text_body()).status_code == 200
    assert client.line.replies == [("rt-1", "echo:こんにちは")]


def test_invalid_signature_is_rejected(client):
    body = text_body()
    response = client.post(
        "/webhook", content=body, headers={"X-Line-Signature": "wrong"}
    )
    assert response.status_code == 400
    assert client.line.replies == []


def test_missing_signature_header_is_rejected(client):
    assert client.post("/webhook", content=text_body()).status_code == 400


def test_group_events_without_a_user_id_are_ignored(client):
    body = json.dumps(
        {
            "destination": "Ud",
            "events": [
                {
                    "type": "message", "mode": "active", "timestamp": 1,
                    "replyToken": "rt", "webhookEventId": "01F",
                    "deliveryContext": {"isRedelivery": False},
                    "source": {"type": "group", "groupId": "Ggg"},
                    "message": {"id": "m", "type": "text", "text": "hi", "quoteToken": "q"},
                }
            ],
        }
    )
    assert post(client, body).status_code == 200
    assert client.line.replies == []


# -- 3.1 loading indicator ----------------------------------------------------


def test_loading_starts_promptly_after_the_webhook(client):
    post(client, text_body())
    assert client.line.loading, "no loading animation was requested"
    user_id, elapsed = client.line.loading[0]
    assert user_id == "Uaaa"
    # The P0 requirement is 3 seconds; in-process this is milliseconds.
    assert elapsed < 3.0


def test_loading_uses_the_configured_window(client, secret_env):
    from app.main import app

    assert app.state.loading.seconds == secret_env.loading_seconds == 20


# -- 3.3 per-user serialisation ----------------------------------------------


def test_two_messages_from_one_user_stay_in_order(client):
    post(client, text_body("いち", reply_token="rt-a"))
    post(client, text_body("に", reply_token="rt-b"))
    assert client.line.replies == [("rt-a", "echo:いち"), ("rt-b", "echo:に")]


def test_several_events_in_one_delivery_are_each_handled(client):
    """LINE batches events. Each must keep its own reply token and text."""
    body = json.dumps(
        {
            "destination": "Ud",
            "events": [
                {
                    "type": "message", "mode": "active", "timestamp": 1700000000000,
                    "replyToken": f"rt-{i}", "webhookEventId": f"01F{i}",
                    "deliveryContext": {"isRedelivery": False},
                    "source": {"type": "user", "userId": "Uaaa"},
                    "message": {"id": f"m{i}", "type": "text",
                                "text": f"メッセージ{i}", "quoteToken": f"q{i}"},
                }
                for i in range(3)
            ],
        }
    )
    assert post(client, body).status_code == 200
    assert client.line.replies == [
        ("rt-0", "echo:メッセージ0"),
        ("rt-1", "echo:メッセージ1"),
        ("rt-2", "echo:メッセージ2"),
    ]


def test_a_batch_mixing_text_and_file_routes_each_correctly(client):
    client.line.content["m-f"] = "予算は1000万円".encode()
    body = json.dumps(
        {
            "destination": "Ud",
            "events": [
                {
                    "type": "message", "mode": "active", "timestamp": 1,
                    "replyToken": "rt-text", "webhookEventId": "01A",
                    "deliveryContext": {"isRedelivery": False},
                    "source": {"type": "user", "userId": "Uaaa"},
                    "message": {"id": "m1", "type": "text", "text": "やあ", "quoteToken": "q"},
                },
                {
                    "type": "message", "mode": "active", "timestamp": 2,
                    "replyToken": "rt-file", "webhookEventId": "01B",
                    "deliveryContext": {"isRedelivery": False},
                    "source": {"type": "user", "userId": "Uaaa"},
                    "message": {"id": "m-f", "type": "file",
                                "fileName": "予算.txt", "fileSize": 20},
                },
            ],
        }
    )
    post(client, body)
    tokens = [token for token, _ in client.line.replies]
    assert tokens == ["rt-text", "rt-file"]
    assert client.line.replies[0][1] == "echo:やあ"
    assert "予算.txt" in client.line.replies[1][1]


# -- 3.5 history --------------------------------------------------------------


def test_history_is_replayed_on_the_next_message(client):
    post(client, text_body("ひとつめ", reply_token="rt-a"))
    post(client, text_body("ふたつめ", reply_token="rt-b"))
    second_call_history = client.claude.seen_history[1]
    assert [(t.role, t.content) for t in second_call_history] == [
        ("user", "ひとつめ"),
        ("assistant", "echo:ひとつめ"),
    ]


def test_history_is_not_shared_between_users(client):
    post(client, text_body("Aのはなし", reply_token="rt-a", user_id="Uaaa"))
    post(client, text_body("Bのはなし", reply_token="rt-b", user_id="Ubbb"))
    assert client.claude.seen_history[1] == []


def test_conversation_history_is_encrypted_on_disk(client, secret_env):
    post(client, text_body("口座番号は1234です"))
    databases = list(secret_env.data_dir.rglob("store.db"))
    assert databases, "no store was created"
    for path in databases:
        assert "口座番号は1234です".encode() not in path.read_bytes()


# -- 3.6 /model ---------------------------------------------------------------


def test_model_command_is_answered_without_calling_claude(client):
    post(client, text_body("/model opus-5", reply_token="rt-m"))
    assert client.line.replies == [("rt-m", "モデルを opus-5 に切り替えました。")]
    assert client.claude.seen_history == []


def test_commands_do_not_enter_the_conversation_history(client):
    post(client, text_body("/model", reply_token="rt-m"))
    post(client, text_body("ふつうの質問", reply_token="rt-q"))
    assert client.claude.seen_history[0] == []


def test_the_default_model_is_fable_5(client):
    """仕様書0章: 既定は claude-fable-5。12.5 フェーズ1検証の項目。"""
    post(client, text_body("こんにちは"))
    assert client.claude.seen_models == ["claude-fable-5"]


def test_switching_to_opus_changes_the_model_passed_to_claude(client):
    post(client, text_body("/model opus-5", reply_token="rt-m"))
    post(client, text_body("そのあとの質問", reply_token="rt-q"))
    assert client.claude.seen_models == ["claude-opus-5"]


def test_switching_back_returns_to_fable(client):
    post(client, text_body("/model opus", reply_token="rt-1"))
    post(client, text_body("いち", reply_token="rt-2"))
    post(client, text_body("/model fable", reply_token="rt-3"))
    post(client, text_body("に", reply_token="rt-4"))
    assert client.claude.seen_models == ["claude-opus-5", "claude-fable-5"]


def test_the_model_choice_does_not_leak_between_users(client):
    post(client, text_body("/model opus-5", reply_token="rt-a", user_id="Uaaa"))
    post(client, text_body("Aの質問", reply_token="rt-b", user_id="Uaaa"))
    post(client, text_body("Bの質問", reply_token="rt-c", user_id="Ubbb"))
    assert client.claude.seen_models == ["claude-opus-5", "claude-fable-5"]


# -- 3.5 file and image ingest ------------------------------------------------


def _docx_bytes(text: str) -> bytes:
    import docx

    document = docx.Document()
    document.add_paragraph(text)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def test_a_text_file_is_summarised_and_stored_encrypted(client, secret_env):
    client.line.content["m-file"] = "決算は前年比12%増でした".encode()
    post(client, file_body("決算.txt"))

    assert client.line.replies[0][0] == "rt-f"
    assert "決算.txt" in client.line.replies[0][1]

    encrypted = list(secret_env.data_dir.rglob("*.enc"))
    # One record for the body, one for the extracted text.
    assert len(encrypted) == 2
    for path in encrypted:
        blob = path.read_bytes()
        assert "決算は前年比12%増でした".encode() not in blob
        assert "決算.txt".encode() not in blob


def test_a_docx_is_parsed_before_summarising(client):
    client.line.content["m-file"] = _docx_bytes("四半期の売上は好調")
    post(client, file_body("q1.docx"))
    assert "四半期の売上は好調" in client.line.replies[0][1]


def test_an_unreadable_file_is_still_stored(client, secret_env):
    client.line.content["m-file"] = b"\x00\x01\x02binary"
    post(client, file_body("mystery.bin"))
    assert "読み取れませんでした" in client.line.replies[0][1]
    # The body is kept even though no text could be extracted.
    assert len(list(secret_env.data_dir.rglob("*.enc"))) == 1


def test_an_oversized_file_is_refused(client, monkeypatch, secret_env):
    from app.line_client import ContentTooLargeError

    async def too_big(_message_id):
        raise ContentTooLargeError("too big")

    client.line.get_message_content = too_big
    post(client, file_body("huge.pdf"))
    assert "大きすぎて" in client.line.replies[0][1]
    assert list(secret_env.data_dir.rglob("*.enc")) == []


def test_an_image_is_described_and_stored(client, secret_env):
    jpeg = b"\xff\xd8\xff\xe0" + b"fake jpeg body"
    client.line.content["m-img"] = jpeg
    post(client, image_body())

    assert client.claude.images == [jpeg]
    assert "image/jpeg" in client.line.replies[0][1]
    stored = list(secret_env.data_dir.rglob("*.enc"))
    assert len(stored) == 1
    assert b"fake jpeg body" not in stored[0].read_bytes()


def test_a_stored_document_is_injected_into_a_later_answer(client):
    client.line.content["m-file"] = "沖縄旅行は3月14日出発、宿はホテルAです".encode()
    post(client, file_body("旅行のしおり.txt"))
    post(client, text_body("沖縄旅行はいつ出発だっけ", reply_token="rt-q"))

    system = client.claude.seen_systems[-1]
    assert "旅行のしおり.txt" in system
    assert "3月14日" in system


def test_an_unrelated_question_does_not_pull_the_document_in(client):
    client.line.content["m-file"] = "沖縄旅行は3月14日出発".encode()
    post(client, file_body("旅行のしおり.txt"))
    post(client, text_body("Pythonのデコレータについて教えて", reply_token="rt-q"))
    assert "参考資料" not in client.claude.seen_systems[-1]
