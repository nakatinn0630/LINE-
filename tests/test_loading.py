import asyncio

import pytest

from app.loading import LoadingIndicator, normalize_loading_seconds


class FakeMessaging:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, int]] = []
        self._fail = fail

    async def show_loading_animation(self, request):
        if self._fail:
            raise RuntimeError("LINE is down")
        self.calls.append((request.chat_id, request.loading_seconds))


@pytest.mark.parametrize("given,expected", [(1, 5), (5, 5), (20, 20), (23, 20), (60, 60), (99, 60)])
def test_loading_seconds_are_clamped_to_the_api_range(given, expected):
    assert normalize_loading_seconds(given) == expected


async def test_start_sends_the_configured_window():
    messaging = FakeMessaging()
    await LoadingIndicator(messaging, seconds=20).start("U1")
    assert messaging.calls == [("U1", 20)]


async def test_a_failed_animation_is_swallowed():
    # The indicator is cosmetic; losing it must never fail the message.
    await LoadingIndicator(FakeMessaging(fail=True)).start("U1")


async def test_keep_alive_refreshes_past_the_60_second_ceiling():
    messaging = FakeMessaging()
    # 5 second window refreshed 0.05s before it lapses.
    indicator = LoadingIndicator(messaging, seconds=5, refresh_margin=4.95)
    async with indicator.keep_alive("U1"):
        await asyncio.sleep(0.26)
    assert len(messaging.calls) >= 4, messaging.calls
    assert all(call == ("U1", 5) for call in messaging.calls)


async def test_keep_alive_stops_when_the_work_finishes():
    messaging = FakeMessaging()
    indicator = LoadingIndicator(messaging, seconds=5, refresh_margin=4.95)
    async with indicator.keep_alive("U1"):
        await asyncio.sleep(0.12)
    before = len(messaging.calls)
    await asyncio.sleep(0.2)
    assert len(messaging.calls) == before
