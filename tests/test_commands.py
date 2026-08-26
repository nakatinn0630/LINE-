import pytest

from app import commands
from app.store import UserStore


@pytest.fixture
def store(tmp_path, crypto):
    return UserStore(tmp_path / "U1" / "store.db", "U1", crypto)


@pytest.mark.parametrize("text,expected", [("/model", True), ("/help", True),
                                          ("こんにちは", False), (" /model", True),
                                          ("model", False)])
def test_is_command(text, expected):
    assert commands.is_command(text) is expected


def test_model_with_no_argument_reports_the_default(secret_env, store):
    """既定は仕様書0章のとおり Opus 5。"""
    reply = commands.handle("/model", settings=secret_env, store=store)
    assert "opus-5" in reply


@pytest.mark.parametrize("arg", ["fable-5", "fable5", "fable", "claude-fable-5"])
def test_switching_away_from_the_default_accepts_aliases(secret_env, store, arg):
    reply = commands.handle(f"/model {arg}", settings=secret_env, store=store)
    assert "fable-5" in reply
    assert store.get_setting("model") == "claude-fable-5"


@pytest.mark.parametrize("arg", ["opus-5", "opus5", "opus", "claude-opus-5"])
def test_switching_back_to_the_default_accepts_aliases(secret_env, store, arg):
    commands.handle("/model fable-5", settings=secret_env, store=store)
    reply = commands.handle(f"/model {arg}", settings=secret_env, store=store)
    assert "opus-5" in reply
    assert store.get_setting("model") == "claude-opus-5"


def test_model_switch_is_per_user(secret_env, tmp_path, crypto):
    one = UserStore(tmp_path / "U1" / "store.db", "U1", crypto)
    two = UserStore(tmp_path / "U2" / "store.db", "U2", crypto)
    commands.handle("/model fable-5", settings=secret_env, store=one)
    assert secret_env.resolve_model(one.get_setting("model")) == "claude-fable-5"
    # 切り替えていないユーザーは既定のまま。
    assert secret_env.resolve_model(two.get_setting("model")) == "claude-opus-5"


def test_unknown_model_is_refused(secret_env, store):
    reply = commands.handle("/model gpt-4", settings=secret_env, store=store)
    assert "切り替えられません" in reply
    assert store.get_setting("model") is None


def test_unknown_command_shows_help(secret_env, store):
    assert "/model" in commands.handle("/nope", settings=secret_env, store=store)


def test_a_stored_model_outside_the_allowlist_falls_back(secret_env, store):
    store.set_setting("model", "claude-retired-9")
    assert secret_env.resolve_model(store.get_setting("model")) == "claude-opus-5"
