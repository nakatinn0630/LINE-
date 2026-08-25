"""Retry policy for Claude calls.

Two failure modes, deliberately handled differently:

* **429 rate limit** - the server tells us exactly how long to wait in the
  `retry-after` header, so we honour it rather than guessing. If that wait is
  long enough that the user would be left staring at nothing, we push them a
  note saying how many seconds it will be.
* **529 overloaded** - there is no meaningful `retry-after` for an overloaded
  upstream, so this backs off exponentially with jitter on its own schedule.

The two have separate attempt counters: a burst of 529s must not consume the
budget for a legitimate 429, and vice versa.

The Anthropic SDK's own retries are switched off by the caller (`max_retries=0`)
so this policy is the only thing retrying, and its waits are the real waits.
"""

from __future__ import annotations

import asyncio
import email.utils
import logging
import random
import time
from dataclasses import dataclass
from typing import Awaitable, Callable, TypeVar

import anthropic

logger = logging.getLogger(__name__)

T = TypeVar("T")

# Used when a 429 arrives with no usable retry-after header.
DEFAULT_RETRY_AFTER_SECONDS = 30.0


@dataclass(frozen=True)
class RetryPolicy:
    max_rate_limit_retries: int = 3
    max_overload_retries: int = 5
    # Above this, the user gets told how long they are waiting.
    notify_threshold_seconds: float = 20.0
    overload_base_seconds: float = 1.0
    overload_max_seconds: float = 60.0
    # A single wait longer than this is treated as not worth waiting out.
    max_single_wait_seconds: float = 300.0


class RetriesExhausted(RuntimeError):
    """Raised when the policy gives up. The last API error is the __cause__."""


def parse_retry_after(value: str | None, *, now: float | None = None) -> float | None:
    """Parse a `retry-after` header: delta-seconds or an HTTP-date."""
    if not value:
        return None
    text = value.strip()
    try:
        return max(0.0, float(text))
    except ValueError:
        pass
    try:
        parsed = email.utils.parsedate_to_datetime(text)
    except (TypeError, ValueError):
        logger.warning("Unparseable retry-after header: %r", value)
        return None
    if parsed is None:
        return None
    reference = time.time() if now is None else now
    return max(0.0, parsed.timestamp() - reference)


def _retry_after_of(exc: anthropic.APIStatusError) -> float | None:
    response = getattr(exc, "response", None)
    headers = getattr(response, "headers", None)
    if headers is None:
        return None
    return parse_retry_after(headers.get("retry-after"))


async def call_with_retries(
    operation: Callable[[], Awaitable[T]],
    *,
    policy: RetryPolicy | None = None,
    notify: Callable[[int], Awaitable[None]] | None = None,
    sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
) -> T:
    """Run `operation`, retrying 429 by retry-after and 529 by backoff.

    `notify` is awaited with the whole-second wait when a 429 forces a wait
    longer than the policy's notify threshold. A failure inside `notify` is
    logged and never blocks the retry.
    """
    policy = policy or RetryPolicy()
    rate_limit_attempts = 0
    overload_attempts = 0

    while True:
        try:
            return await operation()

        except anthropic.RateLimitError as exc:
            rate_limit_attempts += 1
            if rate_limit_attempts > policy.max_rate_limit_retries:
                raise RetriesExhausted(
                    f"rate limited {rate_limit_attempts} times, giving up"
                ) from exc

            wait = _retry_after_of(exc)
            if wait is None:
                wait = DEFAULT_RETRY_AFTER_SECONDS
                logger.warning(
                    "429 with no usable retry-after; falling back to %.0fs.", wait
                )
            if wait > policy.max_single_wait_seconds:
                raise RetriesExhausted(
                    f"429 asked us to wait {wait:.0f}s, beyond the "
                    f"{policy.max_single_wait_seconds:.0f}s ceiling"
                ) from exc

            logger.warning(
                "Rate limited (attempt %d/%d). Waiting %.0fs per retry-after.",
                rate_limit_attempts,
                policy.max_rate_limit_retries,
                wait,
            )
            if notify is not None and wait > policy.notify_threshold_seconds:
                try:
                    await notify(int(round(wait)))
                except Exception:
                    logger.exception("Failed to notify the user about the wait.")
            await sleep(wait)

        except anthropic.OverloadedError as exc:
            overload_attempts += 1
            if overload_attempts > policy.max_overload_retries:
                raise RetriesExhausted(
                    f"overloaded {overload_attempts} times, giving up"
                ) from exc

            wait = backoff_delay(
                overload_attempts,
                base=policy.overload_base_seconds,
                ceiling=policy.overload_max_seconds,
            )
            logger.warning(
                "Upstream overloaded (529, attempt %d/%d). Backing off %.1fs "
                "(retry-after is deliberately ignored here).",
                overload_attempts,
                policy.max_overload_retries,
                wait,
            )
            await sleep(wait)


def backoff_delay(attempt: int, *, base: float, ceiling: float) -> float:
    """Exponential backoff with full jitter, for the 529 path."""
    window = min(ceiling, base * (2 ** (attempt - 1)))
    return random.uniform(0.0, window)
