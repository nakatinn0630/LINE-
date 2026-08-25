import asyncio

import pytest

from app.queues import UserQueues


@pytest.fixture
async def queues():
    q = UserQueues(max_pending=4, idle_timeout=0.2)
    yield q
    await q.aclose()


def job(log, label, delay=0.01):
    async def run():
        await asyncio.sleep(delay)
        log.append(label)
    return run


async def test_one_users_messages_run_in_order(queues):
    log: list[str] = []
    for i in range(4):
        await queues.submit("A", job(log, f"A{i}"))
    await asyncio.sleep(0.3)
    assert log == ["A0", "A1", "A2", "A3"]


async def test_different_users_run_concurrently(queues):
    log: list[str] = []
    await queues.submit("A", job(log, "A", delay=0.15))
    await queues.submit("B", job(log, "B", delay=0.01))
    await asyncio.sleep(0.3)
    # B was submitted second but is quicker, so it finishes first.
    assert log == ["B", "A"]


async def test_a_failing_job_does_not_kill_the_queue(queues):
    log: list[str] = []

    async def boom():
        raise RuntimeError("bad message")

    await queues.submit("A", boom)
    await queues.submit("A", job(log, "after"))
    await asyncio.sleep(0.3)
    assert log == ["after"]


async def test_a_full_queue_is_refused_rather_than_growing(queues):
    async def slow():
        await asyncio.sleep(0.5)

    accepted = [await queues.submit("A", slow) for _ in range(8)]
    assert accepted.count(False) >= 3, accepted


async def test_idle_queues_are_reaped(queues):
    log: list[str] = []
    await queues.submit("A", job(log, "A"))
    await asyncio.sleep(0.5)
    assert queues.pending("A") == 0
    # And a later message still works after the reap.
    await queues.submit("A", job(log, "later"))
    await asyncio.sleep(0.2)
    assert log == ["A", "later"]
