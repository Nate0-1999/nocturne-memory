"""Replay the append-only creation stream; no creation learner is enabled."""

from collections import defaultdict

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from spine.learner.model import identity_is_excluded


async def sweep_unused(session: AsyncSession) -> None:
    """Record each admitted memory's first zero-injection observation, never a verdict."""
    await session.execute(
        text("""
        INSERT INTO creation_outcome
        SELECT 'zero:' || m.id, m.id, m.principal_id, r.origin_machine_id,
          creation_source(m.id), 'zero_injection', 'no injection at sweep', now()
        FROM memory_unit m JOIN memory_revision r ON r.memory_id = m.id AND r.revision = 1
        WHERE m.status = 'active' AND NOT EXISTS (
          SELECT 1 FROM injection_event e WHERE e.memory_id = m.id
          AND e.shown_as IN ('injected','pinned')
        ) ON CONFLICT DO NOTHING
    """)
    )


async def creation_snapshot(session: AsyncSession, principal: str | None) -> dict:
    rows = (
        (
            await session.execute(
                text("""
        SELECT c.* FROM creation_outcome c
        WHERE (CAST(:principal AS text) IS NULL OR c.principal_id = :principal)
        ORDER BY c.ts, c.event_key
    """),
                {"principal": principal},
            )
        )
        .mappings()
        .all()
    )
    # A verification annotation excludes the whole memory's creation cohort.
    annotated = set(
        (
            await session.execute(
                text("""
        SELECT DISTINCT e.memory_id FROM injection_event_annotation a
        JOIN injection_event e ON e.event_uid = a.target_event_uid
        WHERE a.kind = 'verification_only'
        AND (CAST(:principal AS text) IS NULL OR e.principal_id = :principal)
    """),
                {"principal": principal},
            )
        ).scalars()
    )
    excluded = annotated | {
        row["memory_id"]
        for row in rows
        if identity_is_excluded(principal_id=row["principal_id"], machine_id=row["machine_id"])
    }
    events = [dict(row) for row in rows if row["memory_id"] not in excluded]
    groups = defaultdict(lambda: defaultdict(set))
    for event in events:
        groups[event["source"]][event["outcome"]].add(event["memory_id"])
    sources = []
    for source, outcomes in sorted(groups.items()):
        created = outcomes["created"]
        adverse = (
            outcomes["deleted"]
            | outcomes["rejected"]
            | outcomes["never"]
            | outcomes["curator_retired"]
        )
        sources.append(
            {
                "source": source,
                "created": len(created),
                "surviving": len(created - adverse),
                "used": len(created & outcomes["used"]),
                "zero_injection_observed": len(outcomes["zero_injection"]),
                "survival_rate": len(created - adverse) / len(created) if created else None,
                "outcomes": {kind: len(ids) for kind, ids in sorted(outcomes.items())},
            }
        )
    return {
        "sources": sources,
        "events": events,
        "hygiene_excluded_events": len(rows) - len(events),
        "learner": "signals_only",
    }
