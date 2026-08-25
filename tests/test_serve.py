"""The preflight contract with launchd.

exit 0  = "a secret is missing, do not restart me" (KeepAlive.SuccessfulExit=false)
non-zero = a real crash, which launchd should restart.
"""

from __future__ import annotations

import plistlib
import re
from pathlib import Path

import pytest

from app import serve
from app.crypto import generate_master_key

TEMPLATE = Path("deploy/com.line-personal-ai.agent.plist.template")


def _clear_settings():
    from app.config import get_settings

    get_settings.cache_clear()


def test_preflight_passes_when_every_secret_resolves(secret_env):
    assert serve.preflight() is secret_env


@pytest.mark.parametrize(
    "missing",
    ["MASTER_KEY", "LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "ANTHROPIC_API_KEY"],
)
def test_preflight_exits_zero_when_a_secret_is_missing(monkeypatch, tmp_path, missing):
    for name in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "value")
    monkeypatch.setenv("MASTER_KEY", generate_master_key())
    monkeypatch.setenv("LINE_AI_DATA_DIR", str(tmp_path))
    monkeypatch.delenv(missing, raising=False)
    # Make sure the Keychain is never consulted as a fallback on a dev Mac.
    monkeypatch.setattr("app.keychain._read_keychain", lambda service, account: None)
    _clear_settings()

    with pytest.raises(SystemExit) as exit_info:
        serve.preflight()
    assert exit_info.value.code == serve.REFUSE_TO_START == 0
    _clear_settings()


def test_preflight_exits_zero_on_a_malformed_master_key(monkeypatch, tmp_path):
    for name in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "value")
    monkeypatch.setenv("MASTER_KEY", "this-is-not-a-32-byte-base64-key")
    monkeypatch.setenv("LINE_AI_DATA_DIR", str(tmp_path))
    _clear_settings()

    with pytest.raises(SystemExit) as exit_info:
        serve.preflight()
    assert exit_info.value.code == 0
    _clear_settings()


def test_the_missing_key_message_names_the_fix(monkeypatch, tmp_path, caplog):
    for name in ("LINE_CHANNEL_SECRET", "LINE_CHANNEL_ACCESS_TOKEN", "ANTHROPIC_API_KEY"):
        monkeypatch.setenv(name, "value")
    monkeypatch.delenv("MASTER_KEY", raising=False)
    monkeypatch.setenv("LINE_AI_DATA_DIR", str(tmp_path))
    monkeypatch.setattr("app.keychain._read_keychain", lambda service, account: None)
    _clear_settings()

    with caplog.at_level("ERROR"), pytest.raises(SystemExit):
        serve.preflight()
    logged = caplog.text
    assert "MASTER_KEY" in logged
    assert "security add-generic-password" in logged
    _clear_settings()


# -- the plist itself ---------------------------------------------------------


def _rendered_plist() -> dict:
    text = TEMPLATE.read_text()
    text = re.sub(r"__[A-Z_]+__", "placeholder", text)
    return plistlib.loads(text.encode())


def test_plist_template_is_valid_after_substitution():
    assert _rendered_plist()["Label"] == "placeholder"


def test_plist_starts_at_login():
    assert _rendered_plist()["RunAtLoad"] is True


def test_plist_restarts_only_on_abnormal_exit():
    # SuccessfulExit=false is what makes the preflight's exit(0) stick.
    assert _rendered_plist()["KeepAlive"] == {"SuccessfulExit": False}


def test_plist_writes_a_log_file():
    plist = _rendered_plist()
    assert plist["StandardOutPath"].endswith("agent.log")
    assert plist["StandardErrorPath"].endswith("agent.log")


def test_plist_runs_the_preflight_entry_point():
    assert _rendered_plist()["ProgramArguments"][-2:] == ["-m", "app.serve"]
