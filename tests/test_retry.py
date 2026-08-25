"""The 429 and 529 paths, with every wait recorded instead of slept."""

from __future__ import annotations

import email.utils
import time

import anthropic
import httpx2 as httpx
import pytest

from app.retry import (
    RetriesExhausted,
    RetryPolicy,
    backoff_delay,
    call_with_retries,
    parse_retry_after,
)


def _response(status: int, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(
        status_code=status,
        headers=headers or {},
        request=httpx.Request("POST", "https://api.anthropic.com/v1/messages"),
    )


def rate_limited(retry_after: str | None) -> anthropic.RateLimitError:
    headers = {"retry-after": retry_after} if retry_after is not None else {}
    return anthropic.RateLimitError(
        "rate limited", response=_response(429, headers), body=None
    )


def overloaded() -> anthropic.OverloadedError:
    # 529 also carries a retry-after here, to prove the 529 path ignores it.
    return anthropic.OverloadedError(
        "overloaded", response=_response(529, {"retry-after": "999"}), body=None
    )


class Recorder:
    """Stands in for asyncio.sleep and for the push notifier."""

    def __init__(self) -> None:
        self.waits: list[float] = []
        self.notified: list[int] = []

    async def sleep(self, seconds: float) -> None:
        self.waits.append(seconds)

    async def notify(self, seconds: int) -> None:
        self.notified.append(seconds)


def _sequence(*outcomes):
    """An operation that yields each outcome in turn."""
    remaining = list(outcomes)

    async def operation():
        result = remaining.pop(0)
        if isinstance(result, Exception):
            raise result
        return result

    return operation


# -- retry-after parsing ------------------------------------------------------


def test_parse_retry_after_seconds():
    assert parse_retry_after("42") == 42.0


def test_parse_retry_after_http_date():
    when = email.utils.formatdate(time.time() + 30)
    assert 25 <= parse_retry_after(when) <= 31


@pytest.mark.parametrize("value", [None, "", "nonsense"])
def test_parse_retry_after_rejects_junk(value):
    assert parse_retry_after(value) is None


# -- 429 ----------------------------------------------------------------------


async def test_429_waits_exactly_the_retry_after_value():
    recorder = Recorder()
    result = await call_with_retries(
        _sequence(rate_limited("7"), "answer"),
        notify=recorder.notify,
        sleep=recorder.sleep,
    )
    assert result == "answer"
    assert recorder.waits == [7.0]


async def test_429_under_the_threshold_does_not_notify():
    recorder = Recorder()
    await call_with_retries(
        _sequence(rate_limited("20"), "ok"), notify=recorder.notify, sleep=recorder.sleep
    )
    assert recorder.notified == []


async def test_429_over_the_threshold_tells_the_user_how_long():
    recorder = Recorder()
    await call_with_retries(
        _sequence(rate_limited("45"), "ok"), notify=recorder.notify, sleep=recorder.sleep
    )
    assert recorder.notified == [45]
    assert recorder.waits == [45.0]


async def test_429_without_a_header_falls_back_to_a_default():
    recorder = Recorder()
    await call_with_retries(
        _sequence(rate_limited(None), "ok"), notify=recorder.notify, sleep=recorder.sleep
    )
    assert recorder.waits == [30.0]


async def test_429_gives_up_after_the_configured_attempts():
    recorder = Recorder()
    policy = RetryPolicy(max_rate_limit_retries=2)
    with pytest.raises(RetriesExhausted):
        await call_with_retries(
            _sequence(*[rate_limited("1")] * 5), policy=policy, sleep=recorder.sleep
        )
    assert len(recorder.waits) == 2


async def test_429_refuses_an_absurd_wait():
    recorder = Recorder()
    policy = RetryPolicy(max_single_wait_seconds=60)
    with pytest.raises(RetriesExhausted):
        await call_with_retries(
            _sequence(rate_limited("3600"), "ok"), policy=policy, sleep=recorder.sleep
        )
    assert recorder.waits == []


async def test_a_failing_notifier_does_not_stop_the_retry():
    recorder = Recorder()

    async def broken_notify(_seconds: int) -> None:
        raise RuntimeError("push failed")

    result = await call_with_retries(
        _sequence(rate_limited("45"), "ok"), notify=broken_notify, sleep=recorder.sleep
    )
    assert result == "ok"


# -- 529 ----------------------------------------------------------------------


async def test_529_ignores_retry_after_and_backs_off():
    recorder = Recorder()
    policy = RetryPolicy(overload_base_seconds=4.0, overload_max_seconds=64.0)
    result = await call_with_retries(
        _sequence(overloaded(), overloaded(), "ok"),
        policy=policy,
        notify=recorder.notify,
        sleep=recorder.sleep,
    )
    assert result == "ok"
    # The 529 responses carried retry-after: 999 and it was not used.
    assert all(wait <= 8.0 for wait in recorder.waits), recorder.waits
    assert len(recorder.waits) == 2


async def test_529_never_notifies_the_user():
    recorder = Recorder()
    await call_with_retries(
        _sequence(overloaded(), "ok"), notify=recorder.notify, sleep=recorder.sleep
    )
    assert recorder.notified == []


async def test_529_and_429_budgets_are_separate():
    recorder = Recorder()
    policy = RetryPolicy(max_rate_limit_retries=1, max_overload_retries=3)
    # Two 529s would exhaust the 429 budget if they shared one.
    result = await call_with_retries(
        _sequence(overloaded(), overloaded(), rate_limited("2"), "ok"),
        policy=policy,
        sleep=recorder.sleep,
    )
    assert result == "ok"


async def test_529_gives_up_after_the_configured_attempts():
    recorder = Recorder()
    policy = RetryPolicy(max_overload_retries=2)
    with pytest.raises(RetriesExhausted):
        await call_with_retries(
            _sequence(*[overloaded()] * 5), policy=policy, sleep=recorder.sleep
        )
    assert len(recorder.waits) == 2


def test_backoff_grows_and_is_capped():
    windows = [max(backoff_delay(i, base=1, ceiling=8) for _ in range(50)) for i in range(1, 6)]
    assert windows[0] < windows[2]
    assert all(w <= 8.0 for w in windows)


async def test_other_errors_are_not_retried():
    recorder = Recorder()
    boom = anthropic.BadRequestError("bad", response=_response(400), body=None)
    with pytest.raises(anthropic.BadRequestError):
        await call_with_retries(_sequence(boom), sleep=recorder.sleep)
    assert recorder.waits == []
