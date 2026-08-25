"""Per-user serial job queues.

Everything for one user runs one at a time, in arrival order: two messages sent
in quick succession cannot interleave their history writes or answer out of
order. Different users still run concurrently.

Each user gets a queue and a worker task, created on first use and reaped once
the user has been idle for a while.
"""

from __future__ import annotations

import asyncio
import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

Job = Callable[[], Awaitable[None]]


class UserQueues:
    def __init__(self, *, max_pending: int = 8, idle_timeout: float = 300.0) -> None:
        self._max_pending = max_pending
        self._idle_timeout = idle_timeout
        self._queues: dict[str, asyncio.Queue[Job]] = {}
        self._workers: dict[str, asyncio.Task[None]] = {}
        self._lock = asyncio.Lock()
        self._active = 0

    async def submit(self, user_id: str, job: Job) -> bool:
        """Queue `job` for `user_id`. Returns False if that user's queue is full."""
        async with self._lock:
            queue = self._queues.get(user_id)
            if queue is None:
                queue = asyncio.Queue(maxsize=self._max_pending)
                self._queues[user_id] = queue
                self._workers[user_id] = asyncio.create_task(
                    self._worker(user_id, queue), name=f"queue:{user_id}"
                )
            try:
                queue.put_nowait(job)
            except asyncio.QueueFull:
                logger.warning(
                    "Queue for %s is full (%d pending); dropping the job.",
                    user_id,
                    self._max_pending,
                )
                return False
        return True

    def pending(self, user_id: str) -> int:
        queue = self._queues.get(user_id)
        return queue.qsize() if queue else 0

    @property
    def idle(self) -> bool:
        """True when nothing is queued and nothing is mid-flight."""
        return self._active == 0 and all(q.empty() for q in self._queues.values())

    async def join(self, *, poll: float = 0.01) -> None:
        """Wait until every queued job has finished."""
        while not self.idle:
            await asyncio.sleep(poll)

    async def aclose(self) -> None:
        """Cancel every worker. In-flight jobs are abandoned."""
        async with self._lock:
            workers = list(self._workers.values())
            self._workers.clear()
            self._queues.clear()
        for worker in workers:
            worker.cancel()
        await asyncio.gather(*workers, return_exceptions=True)

    async def _worker(self, user_id: str, queue: asyncio.Queue[Job]) -> None:
        while True:
            try:
                job = await asyncio.wait_for(queue.get(), timeout=self._idle_timeout)
            except asyncio.TimeoutError:
                async with self._lock:
                    # Re-check under the lock: a job may have arrived while we
                    # were waiting for it, in which case we must not exit.
                    if queue.empty() and self._queues.get(user_id) is queue:
                        del self._queues[user_id]
                        self._workers.pop(user_id, None)
                        logger.debug("Reaped the idle queue for %s.", user_id)
                        return
                continue
            except asyncio.CancelledError:
                return

            self._active += 1
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:
                # One bad message must not take down the user's whole queue.
                logger.exception("Queued job failed for %s.", user_id)
            finally:
                self._active -= 1
                queue.task_done()
