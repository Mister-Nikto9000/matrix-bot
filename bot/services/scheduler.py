"""Планировщик на asyncio внутри бота, без внешних зависимостей.

Раз в минуту (по границе минуты) вызывает зарегистрированные задачи. Задача получает текущее
время и сама решает, пора ли ей работать; ошибка одной задачи не мешает остальным.
"""
import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import datetime

from bot.services.clock import now_utc, to_msk

log = logging.getLogger(__name__)

Job = Callable[[datetime], Awaitable[None]]


class Scheduler:
    def __init__(self) -> None:
        self.jobs: list[tuple[str, Job]] = []
        self._task: asyncio.Task | None = None

    def add(self, name: str, job: Job) -> None:
        self.jobs.append((name, job))

    async def tick(self, now: datetime | None = None) -> None:
        now = now or now_utc()
        for name, job in self.jobs:
            try:
                await job(now)
            except Exception:  # noqa: BLE001 — одна задача не должна ронять остальные
                log.exception("scheduler job %s failed", name)

    async def _loop(self) -> None:
        while True:
            now = now_utc()
            await asyncio.sleep(60 - now.second - now.microsecond / 1_000_000 + 0.05)
            await self.tick()

    def start(self) -> None:
        self._task = asyncio.create_task(self._loop(), name="scheduler")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass


def msk_hm(now: datetime) -> tuple[int, int]:
    local = to_msk(now)
    return local.hour, local.minute
