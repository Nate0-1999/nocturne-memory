"""M3VZ / A-068: committed, principal-scoped observations of actual curator work."""

from sqlalchemy import text

from spine.curation.contracts import CuratorProgress, CuratorProgressEvent, ProgressPhase


async def record_progress(session_factory, principal_id: str, run_uid: str,
                          phase: ProgressPhase, *, memory_ids=(), finding_uid=None,
                          action=None) -> None:
    # Each transition commits before the provider call; another API worker can read it.
    async with session_factory() as session, session.begin():
        await session.execute(text("""
            INSERT INTO curator_progress
              (principal_id, run_uid, phase, memory_ids, finding_uid, action)
            VALUES (:principal, :run, :phase, :ids, :finding, :action)
        """), {"principal": principal_id, "run": run_uid, "phase": phase,
               "ids": list(memory_ids), "finding": finding_uid, "action": action})


async def read_progress(session_factory, principal_id: str, after: int) -> CuratorProgress:
    async with session_factory() as session:
        rows = (await session.execute(text("""
            SELECT event_id, run_uid, phase, memory_ids, finding_uid, action, ts
            FROM curator_progress WHERE principal_id = :principal AND event_id > :after
            ORDER BY event_id
        """), {"principal": principal_id, "after": after})).mappings().all()
    events = [CuratorProgressEvent.model_validate(dict(row)) for row in rows]
    return CuratorProgress(events=events, cursor=events[-1].event_id if events else after)
