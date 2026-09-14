"""A-051 work notifications wake one process worker without a clock."""

import ast
import asyncio
from pathlib import Path
from uuid import UUID

from spine.learner.service import OptimizationTrigger
from spine.learner.worker import LearnerWorker


class _RecordingService:
    def __init__(self) -> None:
        self.calls: asyncio.Queue[OptimizationTrigger | None] = asyncio.Queue()

    async def retrain_if_due(
        self,
        *,
        optimization_trigger: OptimizationTrigger | None = None,
    ) -> None:
        await self.calls.put(optimization_trigger)


async def test_worker_checks_startup_work_and_subsequent_wakes_then_stops() -> None:
    """A-051 is defended by proving crash catch-up and work wakes share one worker task."""

    service = _RecordingService()
    worker = LearnerWorker(service)  # type: ignore[arg-type]

    worker.start()
    assert await asyncio.wait_for(service.calls.get(), timeout=1.0) is None
    trigger = OptimizationTrigger(event_uid="work-event", thread_id=UUID(int=4))
    worker.notify(trigger)
    assert await asyncio.wait_for(service.calls.get(), timeout=1.0) == trigger
    await worker.stop()


def test_training_calls_have_only_the_worker_and_manual_router_entrypoints() -> None:
    """SPEC D.2 144 / rules-ledger r4-9: a new caller cannot create a second training trigger."""
    root = Path(__file__).parents[1] / "src/spine"
    callers = set()
    for path in root.rglob("*.py"):
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                if node.func.attr in {"retrain", "retrain_if_due", "compact"}:
                    callers.add(path.relative_to(root).as_posix())
    assert callers == {"learner/worker.py", "learner/router.py"}
