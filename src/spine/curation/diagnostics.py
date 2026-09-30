"""Palace Health Reports from stored facts and fixed relevance probes."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.curation.contracts import HealthFinding, PalaceHealthReport
from spine.db.models import CuratorRun, MemoryEdge, MemoryRevision, MemoryUnit
from spine.embeddings import EmbeddingProvider, EmbeddingReceiptContext, embed_one
from spine.inject.scorer import _score_candidate, prompt_keywords
from spine.inject.service import _active_scorer_config, _candidate_from_row

PROBES = (
    "",
    "What should I remember about how this project works?",
    "How should I build, test, and release this project?",
    "Which tools and services does this project use?",
)
PROBE_VERSION = "curator-relevance-v1"
RELEVANCE_MAX_DELTA = 0.015


class HealthReportBuilder:
    """Read one Palace snapshot and name rot without using an LLM."""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        *,
        duplicate_floor: float,
        stale_days: int = 180,
        embedding_provider: EmbeddingProvider | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._duplicate_floor = duplicate_floor
        self._stale_days = stale_days
        self._embedding_provider = embedding_provider
        self._probe_embeddings: dict[str, list[float]] = {}

    async def build(
        self,
        principal_id: str,
        *,
        as_of: datetime | None = None,
    ) -> PalaceHealthReport:
        observed_at = (as_of or datetime.now(UTC)).astimezone(UTC)
        unit = MemoryUnit.__table__
        edge = MemoryEdge.__table__
        async with self._session_factory() as session:
            rows = (
                (
                    await session.execute(
                        select(*unit.c)
                        .where(unit.c.principal_id == principal_id, unit.c.status == "active")
                        .order_by(unit.c.id.asc())
                    )
                )
                .mappings()
                .all()
            )
            active_ids = [row["id"] for row in rows]
            edge_rows = (
                (
                    await session.execute(
                        select(edge.c.from_memory_id, edge.c.to_memory_id, edge.c.edge_type)
                        .where(
                            edge.c.from_memory_id.in_(active_ids),
                            edge.c.to_memory_id.in_(active_ids),
                        )
                        .order_by(
                            edge.c.from_memory_id.asc(),
                            edge.c.to_memory_id.asc(),
                            edge.c.edge_type.asc(),
                        )
                    )
                )
                .all()
                if active_ids
                else []
            )
            latest_run_at = await session.scalar(
                select(func.max(CuratorRun.completed_at)).where(
                    CuratorRun.principal_id == principal_id,
                    CuratorRun.status == "completed",
                )
            )
            revision_rows = (
                (
                    await session.execute(
                        select(MemoryRevision.reason)
                        .join(MemoryUnit, MemoryUnit.id == MemoryRevision.memory_id)
                        .where(
                            MemoryUnit.principal_id == principal_id,
                            *(
                                (MemoryRevision.ts > latest_run_at,)
                                if latest_run_at is not None
                                else ()
                            ),
                        )
                    )
                )
                .scalars()
                .all()
            )
            config = await _active_scorer_config(session) if self._embedding_provider else None
            edits = dict((await session.execute(
                select(MemoryRevision.memory_id, func.max(MemoryRevision.ts))
                .where(MemoryRevision.memory_id.in_(active_ids), MemoryRevision.editor == "user")
                .group_by(MemoryRevision.memory_id)
            )).all()) if config is not None else {}

        vectors: dict[UUID, list[float]] = {row["id"]: [] for row in rows}
        if config is not None and rows:
            candidates = [_candidate_from_row(
                {**row, "last_human_edit_at": edits.get(row["id"])}, pool_sources=("curator",)
            ) for row in rows]
            for prompt in PROBES:
                if prompt not in self._probe_embeddings:
                    self._probe_embeddings[prompt] = await embed_one(
                        self._embedding_provider, prompt, expected_dimensions=1536,
                        receipt_context=EmbeddingReceiptContext(
                            principal_id=principal_id, machine_id="palace-curator",
                            origin_agent="maintenance",
                        ),
                    ) if prompt else [0.0] * 1536
                for candidate in candidates:
                    scored = _score_candidate(
                        candidate, query=tuple(self._probe_embeddings[prompt]),
                        semantic=None if prompt else 0.0, prompt_keywords=prompt_keywords(prompt),
                        snapshot_ts=observed_at, thread_project_key=None, thread_id=None,
                        location_path=None, weights=config.weights_for_project(None),
                        params=config.params, learned_bias=config.bias_offset(candidate.memory_id),
                        axes=config.axes,
                    )
                    vectors[candidate.memory_id].append(scored.score)

        evidence_by_id = {row["id"]: _memory_evidence(row) for row in rows}
        findings: list[tuple[str, tuple[UUID, ...], dict[str, Any]]] = []
        duplicate_links: list[tuple[UUID, UUID]] = []
        for index, left in enumerate(rows):
            for right in rows[index + 1 :]:
                score = _cosine(left["embedding"], right["embedding"])
                left_scores, right_scores = vectors[left["id"]], vectors[right["id"]]
                delta = max((abs(a-b) for a, b in zip(left_scores, right_scores, strict=True)),
                            default=1.0)
                by_relevance = bool(left_scores) and delta <= RELEVANCE_MAX_DELTA
                if score < self._duplicate_floor and not by_relevance:
                    continue
                pair = (left["id"], right["id"])
                duplicate_links.append(pair)
                findings.append(
                    (
                        "duplicate",
                        pair,
                        {
                            "cosine": f"{score:.9f}",
                            "signals": {
                                "embedding": score >= self._duplicate_floor,
                                "relevance": by_relevance,
                            },
                            "relevance": {
                                "probe_version": PROBE_VERSION, "prompts": list(PROBES),
                                "scorer_version": None if config is None else config.version,
                                "left": left_scores, "right": right_scores,
                                "max_delta": delta, "threshold": RELEVANCE_MAX_DELTA,
                            },
                            "memories": [evidence_by_id[memory_id] for memory_id in pair],
                        },
                    )
                )

        contradiction_pairs: set[tuple[UUID, UUID]] = set()
        relates_links: list[tuple[UUID, UUID]] = []
        for left, right, edge_type in edge_rows:
            ordered = tuple(sorted((left, right), key=str))
            if edge_type == "contradicts":
                contradiction_pairs.add(ordered)
            if edge_type == "relates_to":
                relates_links.append(ordered)
        for pair in sorted(contradiction_pairs, key=lambda item: tuple(map(str, item))):
            findings.append(
                (
                    "contradiction",
                    pair,
                    {
                        "edge": "contradicts",
                        "memories": [evidence_by_id[memory_id] for memory_id in pair],
                    },
                )
            )

        stale_before = observed_at - timedelta(days=self._stale_days)
        keyworded = 0
        for row in rows:
            stats = row["stats"] if isinstance(row["stats"], Mapping) else {}
            citations = _count(stats.get("citations"))
            removals = _count(stats.get("removals"))
            keywords = list(row["keywords"])
            if _keywords_are_healthy(keywords):
                keyworded += 1
            else:
                findings.append(
                    (
                        "keyword",
                        (row["id"],),
                        {
                            "problem": "memory does not carry 2-5 distinct lowercase keywords",
                            "memory": evidence_by_id[row["id"]],
                        },
                    )
                )
            if row["updated_at"] < stale_before and citations == 0:
                findings.append(
                    (
                        "stale",
                        (row["id"],),
                        {
                            "stale_days": self._stale_days,
                            "memory": evidence_by_id[row["id"]],
                        },
                    )
                )
            if citations == 0 and removals >= 3:
                findings.append(
                    (
                        "slop",
                        (row["id"],),
                        {
                            "rule": "zero citations and at least three removals",
                            "memory": evidence_by_id[row["id"]],
                        },
                    )
                )

        findings.sort(key=lambda item: (item[0], tuple(map(str, item[1]))))
        contracted: list[HealthFinding] = []
        for ordinal, (kind, memory_ids, evidence) in enumerate(findings):
            fingerprint = _digest(
                {
                    "kind": kind,
                    "memory_ids": [str(value) for value in memory_ids],
                    "revisions": [evidence_by_id[value]["revision"] for value in memory_ids],
                }
            )
            contracted.append(
                HealthFinding(
                    ordinal=ordinal,
                    kind=kind,  # type: ignore[arg-type]
                    memory_ids=list(memory_ids),
                    evidence=evidence,
                    fingerprint=fingerprint,
                )
            )

        corpus_revision = _digest(
            [
                {
                    "id": str(row["id"]),
                    "revision": row["revision"],
                    "status": row["status"],
                    "stats": row["stats"],
                    "keywords": list(row["keywords"]),
                }
                for row in rows
            ]
        )
        clusters = _clusters(active_ids, duplicate_links + relates_links)
        coverage = "100.000" if not rows else f"{keyworded * 100 / len(rows):.3f}"
        return PalaceHealthReport(
            principal_id=principal_id,
            as_of=observed_at,
            corpus_revision=corpus_revision,
            active_units=len(rows),
            clusters=clusters,
            findings=contracted,
            keyword_coverage_percent=coverage,
            stats_delta=_stats_delta(revision_rows),
        )


def _memory_evidence(row: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "memory_id": str(row["id"]),
        "revision": row["revision"],
        "label": row["label"],
        "body": row["body"],
        "kind": row["kind"],
        "keywords": list(row["keywords"]),
        "pin": row["pin"],
        "created_at": row["created_at"].isoformat(),
        "updated_at": row["updated_at"].isoformat(),
        "stats": row["stats"],
    }


def _cosine(left: Sequence[float], right: Sequence[float]) -> float:
    numerator = math.fsum(float(a) * float(b) for a, b in zip(left, right, strict=True))
    left_norm = math.sqrt(math.fsum(float(value) ** 2 for value in left))
    right_norm = math.sqrt(math.fsum(float(value) ** 2 for value in right))
    if left_norm == 0 or right_norm == 0:  # database constraints make this defensive only
        return 0.0
    return max(-1.0, min(1.0, numerator / (left_norm * right_norm)))


def _keywords_are_healthy(keywords: Sequence[str]) -> bool:
    return (
        2 <= len(keywords) <= 5
        and len(set(keywords)) == len(keywords)
        and all(value and value == value.strip() and value == value.lower() for value in keywords)
    )


def _count(value: object) -> int:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _digest(value: object) -> str:
    return sha256(
        json.dumps(value, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    ).hexdigest()


def _clusters(nodes: Sequence[UUID], links: Sequence[tuple[UUID, UUID]]) -> list[list[UUID]]:
    neighbors = {node: set() for node in nodes}
    for left, right in links:
        neighbors[left].add(right)
        neighbors[right].add(left)
    remaining = set(nodes)
    result: list[list[UUID]] = []
    while remaining:
        start = min(remaining, key=str)
        stack = [start]
        component: set[UUID] = set()
        while stack:
            current = stack.pop()
            if current in component:
                continue
            component.add(current)
            stack.extend(sorted(neighbors[current] - component, key=str, reverse=True))
        remaining -= component
        if len(component) > 1:
            result.append(sorted(component, key=str))
    return sorted(result, key=lambda component: tuple(map(str, component)))


def _stats_delta(reasons: Sequence[str]) -> dict[str, int]:
    values = {"revisions": len(reasons), "reinforcements": 0, "merges": 0, "retirements": 0}
    for reason in reasons:
        if reason == "remember/reinforce":
            values["reinforcements"] += 1
        if reason == "merge" or reason.endswith("/merge"):
            values["merges"] += 1
        if reason.endswith("/retire") or reason in {"quarantined", "tombstoned"}:
            values["retirements"] += 1
    return values


__all__ = ["HealthReportBuilder"]
