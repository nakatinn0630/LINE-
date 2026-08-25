"""ClaudeClient against a stub Anthropic server, over real HTTP through the SDK.

This is the automated form of the README's 429 drill: it proves the retry
policy is actually wired into the SDK call, that the wait comes from the
`retry-after` header, and that a long wait produces the push notification.
"""

from __future__ import annotations

import importlib.util
import sys
import threading
from pathlib import Path

import pytest

from app.claude import ClaudeClient
from app.retry import RetryPolicy

STUB_PATH = Path(__file__).resolve().parents[1] / "scripts" / "fake_anthropic.py"


def _load_stub():
    spec = importlib.util.spec_from_file_location("fake_anthropic", STUB_PATH)
    module = importlib.util.module_from_spec(spec)
    sys.modules["fake_anthropic"] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def stub():
    module = _load_stub()
    server = module.serve("127.0.0.1", 0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    module.STATE.update(rate_limit=0, overload=0, retry_after="25", requests=0)
    yield module, f"http://127.0.0.1:{server.server_address[1]}"
    server.shutdown()
    thread.join(timeout=5)


@pytest.fixture
def client(secret_env, stub, monkeypatch):
    _, base_url = stub
    monkeypatch.setenv("ANTHROPIC_BASE_URL", base_url)
    claude = ClaudeClient(secret_env)
    yield claude


class Recorder:
    def __init__(self) -> None:
        self.waits: list[float] = []
        self.notified: list[int] = []

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)

    async def notify(self, seconds: int) -> None:
        self.notified.append(seconds)


@pytest.fixture
def recorder(client, monkeypatch):
    """Record waits instead of serving them, so the tests stay fast."""
    recorder = Recorder()
    import app.claude as claude_module

    original = claude_module.call_with_retries

    async def patched(operation, *, policy=None, notify=None, sleep=None):
        return await original(
            operation, policy=policy, notify=notify, sleep=recorder.sleep
        )

    monkeypatch.setattr(claude_module, "call_with_retries", patched)
    return recorder


async def test_a_plain_request_succeeds(client):
    reply = await client.reply(
        model="claude-opus-5", system="s", history=[], user_text="hello"
    )
    assert "スタブからの応答です" in reply
    await client.aclose()


async def test_the_sdk_does_not_retry_behind_our_back(client, stub):
    module, _ = stub
    module.STATE["rate_limit"] = 99
    client._policy = RetryPolicy(max_rate_limit_retries=0)
    reply = await client.reply(
        model="claude-opus-5", system="s", history=[], user_text="hello"
    )
    # One request, not three: max_retries=0 on the SDK client.
    assert module.STATE["requests"] == 1
    assert "混み合っている" in reply
    await client.aclose()


async def test_429_waits_the_header_value_then_succeeds(client, stub, recorder):
    module, _ = stub
    module.STATE["rate_limit"] = 1
    module.STATE["retry_after"] = "25"

    reply = await client.reply(
        model="claude-opus-5", system="s", history=[], user_text="hello",
        notify=recorder.notify,
    )
    assert "スタブからの応答です" in reply
    assert recorder.waits == [25.0]
    assert recorder.notified == [25], "a 25s wait should tell the user"
    assert module.STATE["requests"] == 2
    await client.aclose()


async def test_a_short_429_wait_does_not_notify(client, stub, recorder):
    module, _ = stub
    module.STATE["rate_limit"] = 1
    module.STATE["retry_after"] = "5"

    await client.reply(
        model="claude-opus-5", system="s", history=[], user_text="hello",
        notify=recorder.notify,
    )
    assert recorder.waits == [5.0]
    assert recorder.notified == []
    await client.aclose()


async def test_529_backs_off_and_ignores_retry_after(client, stub, recorder):
    module, _ = stub
    # The stub sends retry-after: 999 on its 529s.
    module.STATE["overload"] = 2

    reply = await client.reply(
        model="claude-opus-5", system="s", history=[], user_text="hello",
        notify=recorder.notify,
    )
    assert "スタブからの応答です" in reply
    assert len(recorder.waits) == 2
    assert all(wait < 10 for wait in recorder.waits), recorder.waits
    assert recorder.notified == [], "529 must not push a wait estimate"
    await client.aclose()
