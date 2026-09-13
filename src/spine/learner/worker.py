"""One process-local wake worker for A-051 work-based retraining."""

from __future__ import annotations

import asyncio
import logging

from spine.learner.service import LearnerService, OptimizationTrigger

logger = logging.getLogger(__name__)


class LearnerWorker:
    """Coalesce work notifications without putting retraining on request paths."""

    def __init__(self, service: LearnerService) -> None:
        self._service = service
        self._wake = asyncio.Event()
        self._stop = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._pending_trigger: OptimizationTrigger | None = None

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("learner worker is already running")
        self._stop.clear()
        self._task = asyncio.create_task(self._run(), name="chrysopoeia-learner")

    def notify(self, trigger: OptimizationTrigger | None = None) -> None:
        """Wake the singleton worker; no caller creates or awaits a learner task."""

        if trigger is not None:
            self._pending_trigger = trigger
        self._wake.set()

    async def stop(self) -> None:
        task = self._task
        if task is None:
            return
        self._stop.set()
        self._wake.set()
        await task
        self._task = None

    async def _run(self) -> None:
        while True:
            await self._wake.wait()
            self._wake.clear()
            if self._stop.is_set():
                return
            try:
                trigger = self._pending_trigger
                self._pending_trigger = None
                if trigger is not None:
                    await self._service.compact(trigger)
            except Exception:
                logger.exception("Background Chrysopoeia retrain failed")


__all__ = ["LearnerWorker"]
