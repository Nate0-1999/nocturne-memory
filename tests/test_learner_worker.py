"""A-051 work notifications wake one process worker without a clock."""

import asyncio
from uuid import UUID

from spine.learner.service import OptimizationTrigger
from spine.learner.worker import LearnerWorker


class _RecordingService:
    def __init__(self) -> None:
        self.calls: asyncio.Queue[OptimizationTrigger | None] = asyncio.Queue()

    async def compact(self, trigger: OptimizationTrigger) -> None:
        await self.calls.put(trigger)


async def test_worker_waits_for_real_compaction_instead_of_startup_or_stride() -> None:
    """D.2 144/153: no optimization before a main-thread compaction event."""

    service = _RecordingService()
    worker = LearnerWorker(service)  # type: ignore[arg-type]

    worker.start()
    await asyncio.sleep(0)
    assert service.calls.empty()
    trigger = OptimizationTrigger(event_uid="work-event", thread_id=UUID(int=4))
    worker.notify(trigger)
    assert await asyncio.wait_for(service.calls.get(), timeout=1.0) == trigger
    await worker.stop()
