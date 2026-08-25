"""Runtime configuration.

Secrets come from the Keychain (see `app.keychain`). Everything else is a
non-sensitive tunable that can be set as a plain environment variable. The
tunables are prefixed `LINE_AI_` so they cannot collide with the generic
`CLAUDE_*` variables other tools already define.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

from app.crypto import MASTER_KEY_ACCOUNT, decode_master_key
from app.keychain import DEFAULT_SERVICE, get_secret

# Keychain account names. These double as the environment-variable override names.
LINE_CHANNEL_SECRET = "LINE_CHANNEL_SECRET"
LINE_CHANNEL_ACCESS_TOKEN = "LINE_CHANNEL_ACCESS_TOKEN"
ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"
MASTER_KEY = MASTER_KEY_ACCOUNT

DEFAULT_MODEL = "claude-opus-5"
# Models the /model command will switch between.
SELECTABLE_MODELS: tuple[str, ...] = ("claude-opus-5", "claude-fable-5")

DEFAULT_SYSTEM_PROMPT = (
    "You are a personal assistant that talks to one person through LINE. "
    "Reply in the language the user writes in. "
    "Keep replies short and conversational - a few sentences at most, since they "
    "are read on a phone. Use no Markdown formatting: LINE renders it as literal "
    "characters. If you are unsure about something, say so plainly."
)


@dataclass(frozen=True)
class Settings:
    line_channel_secret: str
    line_channel_access_token: str
    anthropic_api_key: str
    master_key: bytes
    keychain_service: str
    data_dir: Path

    model: str
    max_tokens: int
    effort: str
    system_prompt: str
    refusal_fallback: bool

    # Conversation memory
    history_exchanges: int
    compact_after_messages: int
    summary_max_tokens: int
    summary_input_chars: int

    # Document retrieval
    document_limit: int
    document_threshold: float
    document_inject_chars: int
    max_download_bytes: int

    # Behaviour
    loading_seconds: int
    notify_wait_threshold_seconds: float
    queue_max_pending: int

    selectable_models: tuple[str, ...] = field(default=SELECTABLE_MODELS)
    # LINE rejects text messages longer than 5000 characters.
    max_reply_chars: int = 4900

    def resolve_model(self, requested: str | None) -> str:
        """Return the user's chosen model if it is one we allow, else the default."""
        if requested and requested in self.selectable_models:
            return requested
        return self.model


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process. Raises SecretNotFoundError if a secret is missing."""
    service = os.environ.get("LINE_AI_KEYCHAIN_SERVICE", DEFAULT_SERVICE)
    return Settings(
        line_channel_secret=get_secret(LINE_CHANNEL_SECRET, service=service),
        line_channel_access_token=get_secret(LINE_CHANNEL_ACCESS_TOKEN, service=service),
        anthropic_api_key=get_secret(ANTHROPIC_API_KEY, service=service),
        master_key=decode_master_key(get_secret(MASTER_KEY, service=service)),
        keychain_service=service,
        data_dir=Path(os.environ.get("LINE_AI_DATA_DIR", "data")).resolve(),
        model=os.environ.get("LINE_AI_MODEL", DEFAULT_MODEL),
        max_tokens=int(os.environ.get("LINE_AI_MAX_TOKENS", "2000")),
        # LINE reply tokens are short-lived, so favour a fast turnaround over
        # deep reasoning. Raise to "high" if you want more careful answers.
        effort=os.environ.get("LINE_AI_EFFORT", "low"),
        system_prompt=os.environ.get("LINE_AI_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        refusal_fallback=os.environ.get("LINE_AI_REFUSAL_FALLBACK", "1") != "0",
        history_exchanges=int(os.environ.get("LINE_AI_HISTORY_EXCHANGES", "8")),
        compact_after_messages=int(os.environ.get("LINE_AI_COMPACT_AFTER", "40")),
        summary_max_tokens=int(os.environ.get("LINE_AI_SUMMARY_MAX_TOKENS", "1200")),
        summary_input_chars=int(os.environ.get("LINE_AI_SUMMARY_INPUT_CHARS", "60000")),
        document_limit=int(os.environ.get("LINE_AI_DOCUMENT_LIMIT", "3")),
        document_threshold=float(os.environ.get("LINE_AI_DOCUMENT_THRESHOLD", "0.08")),
        document_inject_chars=int(os.environ.get("LINE_AI_DOCUMENT_CHARS", "12000")),
        max_download_bytes=int(
            os.environ.get("LINE_AI_MAX_DOWNLOAD_BYTES", str(20 * 1024 * 1024))
        ),
        loading_seconds=int(os.environ.get("LINE_AI_LOADING_SECONDS", "20")),
        notify_wait_threshold_seconds=float(
            os.environ.get("LINE_AI_NOTIFY_WAIT_THRESHOLD", "20")
        ),
        queue_max_pending=int(os.environ.get("LINE_AI_QUEUE_MAX_PENDING", "8")),
    )
