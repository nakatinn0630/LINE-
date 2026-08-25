"""Runtime configuration.

Secrets come from the Keychain (see `app.keychain`). Everything else is a
non-sensitive tunable that can be set as a plain environment variable. The
tunables are prefixed `LINE_AI_` so they cannot collide with the generic
`CLAUDE_*` variables other tools already define.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache

from app.keychain import DEFAULT_SERVICE, get_secret

# Keychain account names. These double as the environment-variable override names.
LINE_CHANNEL_SECRET = "LINE_CHANNEL_SECRET"
LINE_CHANNEL_ACCESS_TOKEN = "LINE_CHANNEL_ACCESS_TOKEN"
ANTHROPIC_API_KEY = "ANTHROPIC_API_KEY"

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
    keychain_service: str
    model: str
    max_tokens: int
    effort: str
    system_prompt: str
    refusal_fallback: bool
    # LINE rejects text messages longer than 5000 characters.
    max_reply_chars: int = 4900


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Load settings once per process. Raises SecretNotFoundError if a secret is missing."""
    service = os.environ.get("LINE_AI_KEYCHAIN_SERVICE", DEFAULT_SERVICE)
    return Settings(
        line_channel_secret=get_secret(LINE_CHANNEL_SECRET, service=service),
        line_channel_access_token=get_secret(LINE_CHANNEL_ACCESS_TOKEN, service=service),
        anthropic_api_key=get_secret(ANTHROPIC_API_KEY, service=service),
        keychain_service=service,
        model=os.environ.get("LINE_AI_MODEL", "claude-opus-5"),
        max_tokens=int(os.environ.get("LINE_AI_MAX_TOKENS", "2000")),
        # LINE reply tokens are short-lived, so favour a fast turnaround over
        # deep reasoning. Raise to "high" if you want more careful answers.
        effort=os.environ.get("LINE_AI_EFFORT", "low"),
        system_prompt=os.environ.get("LINE_AI_SYSTEM_PROMPT", DEFAULT_SYSTEM_PROMPT),
        refusal_fallback=os.environ.get("LINE_AI_REFUSAL_FALLBACK", "1") != "0",
    )
