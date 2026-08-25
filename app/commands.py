"""Slash commands typed into the chat.

Commands are handled before anything reaches Claude, so they cost nothing and
never end up in the conversation history.
"""

from __future__ import annotations

import logging

from app.config import Settings
from app.store import UserStore

logger = logging.getLogger(__name__)

MODEL_SETTING_KEY = "model"

# Short names accepted in place of the full model id.
_ALIASES = {
    "opus": "claude-opus-5",
    "opus-5": "claude-opus-5",
    "opus5": "claude-opus-5",
    "fable": "claude-fable-5",
    "fable-5": "claude-fable-5",
    "fable5": "claude-fable-5",
}

_HELP = (
    "使えるコマンド:\n"
    "/model - いま使っているモデルを表示\n"
    "/model opus-5 - 応答の速いモデルに切り替え\n"
    "/model fable-5 - じっくり考えるモデルに切り替え\n"
    "/help - このヘルプ"
)


def is_command(text: str) -> bool:
    return text.strip().startswith("/")


def handle(text: str, *, settings: Settings, store: UserStore) -> str:
    """Run a command and return the reply to send. Assumes `is_command(text)`."""
    parts = text.strip().split()
    name = parts[0].lower()
    args = parts[1:]

    if name == "/help":
        return _HELP
    if name == "/model":
        return _model_command(args, settings=settings, store=store)
    return f"知らないコマンドです: {name}\n\n{_HELP}"


def _model_command(args: list[str], *, settings: Settings, store: UserStore) -> str:
    current = settings.resolve_model(store.get_setting(MODEL_SETTING_KEY))

    if not args:
        options = " / ".join(_short(m) for m in settings.selectable_models)
        return f"いまのモデル: {_short(current)}\n切り替え: /model <{options}>"

    requested = _ALIASES.get(args[0].lower(), args[0])
    if requested not in settings.selectable_models:
        options = " / ".join(_short(m) for m in settings.selectable_models)
        return f"{args[0]} には切り替えられません。選べるのは {options} です。"

    store.set_setting(MODEL_SETTING_KEY, requested)
    logger.info("Model switched to %s.", requested)
    return f"モデルを {_short(requested)} に切り替えました。"


def _short(model_id: str) -> str:
    return model_id.removeprefix("claude-")
