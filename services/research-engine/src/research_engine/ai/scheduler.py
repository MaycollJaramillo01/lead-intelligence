"""The GPU queue and the circuit breaker of the local AI layer.

One queue owns the GPU: work runs one item at a time (two models never load together in 8 GB and a
model never answers two requests that would double its KV cache). Items carry a priority (a person
waiting before a project run before a benchmark), an owner (a job, the benchmark) for cancellation,
and a group (the model): among equal priorities the queue keeps serving the loaded model, so
compatible work runs back to back instead of swapping models. The queue has a capacity; beyond it a
submission is refused at once (backpressure), never buffered without bound.

The breaker counts failures that say the runtime is unwell (unreachable, timeouts, 5xx). After
THRESHOLD in a row it opens: calls are refused without touching the runtime for a cooldown that
doubles up to MAX_COOLDOWN_S; then one call is let through (half-open) and its result closes or
reopens it. Nothing retries in a loop.
"""

import asyncio
import contextlib
import heapq
import itertools
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import IntEnum
from typing import Any, Final

from research_engine.domain import AiCircuitState

CAPACITY: Final = 64
THRESHOLD: Final = 3
COOLDOWN_S: Final = 30.0
MAX_COOLDOWN_S: Final = 300.0


class Priority(IntEnum):
    INTERACTIVE = 0
    JOB = 5
    BENCHMARK = 9


class QueueFullError(RuntimeError):
    """The queue is at capacity: try again once work has drained."""


@dataclass(order=True, slots=True)
class _Item:
    priority: int
    sequence: int
    owner: str = field(compare=False)
    group: str = field(compare=False)
    work: Callable[[], Awaitable[Any]] = field(compare=False)
    future: "asyncio.Future[Any]" = field(compare=False)


class GpuQueue:
    def __init__(self, capacity: int = CAPACITY) -> None:
        self.capacity = capacity
        self._heap: list[_Item] = []
        self._sequence = itertools.count()
        self._wake = asyncio.Event()
        self._running: tuple[_Item, asyncio.Task[Any]] | None = None
        self._last_group: str | None = None
        self._consumer: asyncio.Task[None] | None = None

    @property
    def queued(self) -> int:
        return len(self._heap)

    @property
    def busy(self) -> bool:
        return self._running is not None or bool(self._heap)

    def running_group(self) -> str | None:
        return self._running[0].group if self._running else None

    def submit[T](
        self, work: Callable[[], Awaitable[T]], *, priority: Priority, owner: str, group: str
    ) -> "asyncio.Future[T]":
        if len(self._heap) >= self.capacity:
            raise QueueFullError(f"the local AI queue is full ({self.capacity} waiting)")
        loop = asyncio.get_running_loop()
        future: asyncio.Future[T] = loop.create_future()
        heapq.heappush(
            self._heap, _Item(priority, next(self._sequence), owner, group, work, future)
        )
        if self._consumer is None or self._consumer.done():
            self._consumer = loop.create_task(self._consume(), name="ai-gpu-queue")
        self._wake.set()
        return future

    def cancel_owner(self, owner: str) -> int:
        """Cancels the owner's queued items and the running one. Returns how many."""
        kept, cancelled = [], 0
        for item in self._heap:
            if item.owner == owner:
                item.future.cancel()
                cancelled += 1
            else:
                kept.append(item)
        heapq.heapify(kept)
        self._heap = kept
        if self._running is not None and self._running[0].owner == owner:
            self._running[1].cancel()
            cancelled += 1
        return cancelled

    def cancel_all(self) -> None:
        for owner in {item.owner for item in self._heap} | (
            {self._running[0].owner} if self._running else set()
        ):
            self.cancel_owner(owner)

    def _pop(self) -> _Item:
        """The best priority; within it, the loaded model's work first, then arrival order."""
        best = self._heap[0].priority
        same = [
            item for item in self._heap if item.priority == best and item.group == self._last_group
        ]
        chosen = min(same) if same else self._heap[0]
        self._heap.remove(chosen)
        heapq.heapify(self._heap)
        return chosen

    async def _consume(self) -> None:
        while True:
            if not self._heap:
                self._wake.clear()
                try:
                    await asyncio.wait_for(self._wake.wait(), timeout=60)
                except TimeoutError:
                    if not self._heap:
                        return
                continue
            item = self._pop()
            if item.future.cancelled():
                continue
            task = asyncio.ensure_future(item.work())
            self._running = (item, task)
            self._last_group = item.group
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                if not task.done():  # the consumer itself is being cancelled
                    task.cancel()
                    with contextlib.suppress(BaseException):
                        await task
                    if not item.future.done():
                        item.future.cancel()
                    raise
                if not item.future.done():
                    item.future.cancel()
            except BaseException as exc:  # noqa: BLE001 - handed to the submitter
                if not item.future.done():
                    item.future.set_exception(exc)
            else:
                if not item.future.done():
                    item.future.set_result(result)
            finally:
                self._running = None

    async def close(self) -> None:
        self.cancel_all()
        if self._consumer is not None:
            self._consumer.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._consumer


class CircuitBreaker:
    def __init__(
        self,
        threshold: int = THRESHOLD,
        cooldown_s: float = COOLDOWN_S,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._base = cooldown_s
        self._clock = clock
        self.failures = 0
        self.last_error: str | None = None
        self._cooldown = cooldown_s
        self._open_until: float | None = None
        self._probing = False

    @property
    def state(self) -> AiCircuitState:
        if self._open_until is None:
            return AiCircuitState.CLOSED
        return (
            AiCircuitState.HALF_OPEN if self._clock() >= self._open_until else AiCircuitState.OPEN
        )

    def seconds_open(self) -> float | None:
        if self._open_until is None:
            return None
        return max(0.0, self._open_until - self._clock())

    def allow(self) -> bool:
        """False while open; once the cooldown ends, lets exactly one call through."""
        state = self.state
        if state is AiCircuitState.CLOSED:
            return True
        if state is AiCircuitState.HALF_OPEN and not self._probing:
            self._probing = True
            return True
        return False

    def success(self) -> None:
        self.failures, self._open_until, self._probing = 0, None, False
        self._cooldown = self._base

    def failure(self, message: str) -> None:
        self.failures += 1
        self.last_error = message[:500]
        if self._probing:
            self._cooldown = min(MAX_COOLDOWN_S, self._cooldown * 2)
        if self._probing or self.failures >= self._threshold:
            self._open_until = self._clock() + self._cooldown
        self._probing = False

    def release(self) -> None:
        """A half-open call that ended without saying anything about the runtime."""
        self._probing = False
