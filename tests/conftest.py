"""Shared fixtures. No test ever touches the real Keychain or the real data dir."""

from __future__ import annotations

import pytest

from app.crypto import Crypto, decode_master_key, generate_master_key

TEST_CHANNEL_SECRET = "test-channel-secret"


@pytest.fixture
def master_key() -> bytes:
    return decode_master_key(generate_master_key())


@pytest.fixture
def crypto(master_key: bytes) -> Crypto:
    return Crypto(master_key)


@pytest.fixture
def secret_env(monkeypatch, tmp_path):
    """Point every secret and the data directory somewhere disposable."""
    monkeypatch.setenv("LINE_CHANNEL_SECRET", TEST_CHANNEL_SECRET)
    monkeypatch.setenv("LINE_CHANNEL_ACCESS_TOKEN", "test-access-token")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "test-anthropic-key")
    monkeypatch.setenv("MASTER_KEY", generate_master_key())
    monkeypatch.setenv("LINE_AI_DATA_DIR", str(tmp_path / "data"))

    from app.config import get_settings

    get_settings.cache_clear()
    yield get_settings()
    get_settings.cache_clear()
