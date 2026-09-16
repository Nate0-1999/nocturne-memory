"""Literal SQLAlchemy mappings for the authoritative SPEC C.2 DDL."""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    REAL,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    Text,
    UniqueConstraint,
    desc,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, TSVECTOR
from sqlalchemy.dialects.postgresql import UUID as PGUUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    """Declarative base for Spine-owned authoritative tables."""


class MemoryUnit(Base):
    """The mutable cloud head for one atomic memory unit."""

    __tablename__ = "memory_unit"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('fact','preference','procedure','project_note','persona','pinned')",
            name="memory_unit_kind_check",
        ),
        CheckConstraint(
            "status IN ('active','candidate','staged','quarantined','tombstoned')",
            name="memory_unit_status_check",
        ),
        CheckConstraint(
            "(run_id IS NULL) = (origin_agent IS NULL)",
            name="memory_unit_run_lineage_pair_check",
        ),
        CheckConstraint(
            "status <> 'staged' OR run_id IS NOT NULL",
            name="memory_unit_staged_lineage_check",
        ),
        Index(
            "memory_unit_embedding_idx",
            "embedding",
            postgresql_using="hnsw",
            postgresql_ops={"embedding": "vector_cosine_ops"},
        ),
        Index(
            "memory_unit_search_tsv_idx",
            "search_tsv",
            postgresql_using="gin",
        ),
        Index(
            "memory_unit_principal_id_status_project_key_idx",
            "principal_id",
            "status",
            "project_key",
        ),
        Index(
            "memory_unit_principal_run_origin_status_idx",
            "principal_id",
            "run_id",
            "origin_agent",
            "status",
        ),
        Index(
            "memory_unit_principal_origin_thread_idx",
            "principal_id",
            "origin_thread_id",
            postgresql_where=text("origin_thread_id IS NOT NULL"),
        ),
        Index(
            "memory_unit_active_label",
            "principal_id",
            "label",
            unique=True,
            postgresql_where=text("status = 'active'"),
        ),
    )

    id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        primary_key=True,
        server_default=text("gen_random_uuid()"),
    )
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    keywords: Mapped[list[str]] = mapped_column(
        ARRAY(Text),
        nullable=False,
        server_default=text("'{}'"),
    )
    search_tsv: Mapped[Any] = mapped_column(
        TSVECTOR,
        nullable=False,
        comment="M2E lexical candidate document over label, body, and keywords.",
    )
    embedding: Mapped[list[float]] = mapped_column(Vector(1536), nullable=False)
    embedding_model: Mapped[str] = mapped_column(Text, nullable=False)
    project_key: Mapped[str | None] = mapped_column(Text)
    thread_origin: Mapped[str | None] = mapped_column(Text)
    origin_thread_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    origin_path: Mapped[str | None] = mapped_column(Text)
    origin_location: Mapped[str | None] = mapped_column(Text)
    run_id: Mapped[str | None] = mapped_column(Text)
    origin_agent: Mapped[str | None] = mapped_column(Text)
    pin: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    status: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'active'"),
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("1"))
    stats: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text(
            '\'{"injections"\\:0,"removals"\\:0,"citations"\\:0,'
            '"never_kills"\\:0,"last_injected_at"\\:null}\'::jsonb'
        ),
    )
    bias: Mapped[float] = mapped_column(REAL, nullable=False, server_default=text("0.0"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class MemoryRevision(Base):
    """One append-only memory head or divergent lineage revision."""

    __tablename__ = "memory_revision"
    __table_args__ = (Index("memory_revision_memory_id_ts_idx", "memory_id", "ts"),)

    rev_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    parent_uid: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("memory_revision.rev_uid", name="memory_revision_parent_uid_fkey"),
    )
    memory_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True),
        ForeignKey("memory_unit.id", name="memory_revision_memory_id_fkey"),
        nullable=False,
    )
    revision: Mapped[int | None] = mapped_column(Integer)
    body: Mapped[str] = mapped_column(Text, nullable=False)
    label: Mapped[str] = mapped_column(Text, nullable=False)
    editor: Mapped[str] = mapped_column(Text, nullable=False)
    origin_machine_id: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("''"))
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class MemoryEdge(Base):
    """Append-only M2H verdict lineage between memory heads."""

    __tablename__ = "memory_edge"
    __table_args__ = (
        CheckConstraint(
            "edge_type IN ('merged_from','supersedes','contradicts','relates_to')",
            name="memory_edge_type_check",
        ),
        UniqueConstraint("from_memory_id", "to_memory_id", "edge_type"),
    )

    edge_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    from_memory_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memory_unit.id"), nullable=False
    )
    to_memory_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memory_unit.id"), nullable=False
    )
    edge_type: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class ApprovalQueueItem(Base):
    """One durable typed consent card for a candidate memory."""

    __tablename__ = "approval_queue_item"
    __table_args__ = (
        CheckConstraint(
            "verdict IN ('new','merge','supersede','contradict','retire','keyword_repair','split')",
            name="approval_queue_item_verdict_check",
        ),
        CheckConstraint(
            "state IN ('pending','approved','rejected')",
            name="approval_queue_item_state_check",
        ),
        CheckConstraint(
            "birthplace IN ('thread','seed','symphony','curator')",
            name="approval_queue_item_birthplace_check",
        ),
        CheckConstraint(
            "(birthplace = 'thread' AND birthplace_thread_id IS NOT NULL "
            "AND batch_uid IS NULL AND source_name IS NULL AND source_sha256 IS NULL "
            "AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL "
            "AND judged_context IS NULL AND curator_run_uid IS NULL "
            "AND curator_finding_uid IS NULL AND proposal_payload IS NULL "
            "AND candidate_revision IS NULL) OR "
            "(birthplace = 'seed' AND birthplace_thread_id IS NULL "
            "AND batch_uid IS NOT NULL AND source_name IS NOT NULL AND source_sha256 IS NOT NULL "
            "AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL "
            "AND judged_context IS NULL AND curator_run_uid IS NULL "
            "AND curator_finding_uid IS NULL AND proposal_payload IS NULL "
            "AND candidate_revision IS NULL) OR "
            "(birthplace = 'symphony' AND birthplace_thread_id IS NULL "
            "AND batch_uid IS NOT NULL AND source_name IS NULL AND source_sha256 IS NULL "
            "AND birthplace_run_id IS NOT NULL AND birthplace_origin_agent IS NOT NULL "
            "AND judged_context IS NOT NULL AND curator_run_uid IS NULL "
            "AND curator_finding_uid IS NULL AND proposal_payload IS NULL "
            "AND candidate_revision IS NULL) OR "
            "(birthplace = 'curator' AND birthplace_thread_id IS NULL "
            "AND batch_uid IS NULL AND source_name IS NULL AND source_sha256 IS NULL "
            "AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL "
            "AND judged_context IS NULL AND curator_run_uid IS NOT NULL "
            "AND curator_finding_uid IS NOT NULL AND proposal_payload IS NOT NULL "
            "AND candidate_revision IS NOT NULL)",
            name="approval_queue_item_birthplace_shape_check",
        ),
        UniqueConstraint("curator_finding_uid"),
        Index(
            "approval_queue_item_non_curator_candidate_uidx",
            "candidate_memory_id",
            unique=True,
            postgresql_where=text("birthplace <> 'curator'"),
        ),
        Index("approval_queue_item_principal_state_idx", "principal_id", "state"),
        Index("approval_queue_item_thread_state_idx", "birthplace_thread_id", "state"),
        Index("approval_queue_item_batch_state_idx", "batch_uid", "state"),
    )

    item_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    candidate_memory_id: Mapped[UUID] = mapped_column(
        PGUUID(as_uuid=True), ForeignKey("memory_unit.id"), nullable=False
    )
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    birthplace: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'thread'"))
    birthplace_thread_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    batch_uid: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    source_name: Mapped[str | None] = mapped_column(Text)
    source_sha256: Mapped[str | None] = mapped_column(Text)
    birthplace_run_id: Mapped[str | None] = mapped_column(Text)
    birthplace_origin_agent: Mapped[str | None] = mapped_column(Text)
    judged_context: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    candidate_revision: Mapped[int | None] = mapped_column(Integer)
    curator_run_uid: Mapped[str | None] = mapped_column(Text)
    curator_finding_uid: Mapped[str | None] = mapped_column(Text)
    proposal_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    neighbor_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    target_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    state: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ApprovalDecision(Base):
    """Append-only owner/passive disposition for one queue card."""

    __tablename__ = "approval_decision"
    __table_args__ = (
        CheckConstraint("decision IN ('approve','deny')", name="approval_decision_value_check"),
        CheckConstraint(
            "approval_mode IN ('explicit','passive')",
            name="approval_decision_mode_check",
        ),
        CheckConstraint(
            "actor_class IN ('human','passive')",
            name="approval_decision_actor_check",
        ),
        UniqueConstraint("item_uid"),
    )

    decision_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    item_uid: Mapped[str] = mapped_column(
        Text, ForeignKey("approval_queue_item.item_uid"), nullable=False
    )
    decision: Mapped[str] = mapped_column(Text, nullable=False)
    approval_mode: Mapped[str] = mapped_column(Text, nullable=False)
    actor_class: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CuratorTriggerState(Base):
    """Durable admitted-write cursor for work-triggered curator runs."""

    __tablename__ = "curator_trigger_state"
    __table_args__ = (
        CheckConstraint("admitted_writes >= 0", name="curator_trigger_admitted_check"),
        CheckConstraint(
            "last_run_writes >= 0 AND last_run_writes <= admitted_writes",
            name="curator_trigger_cursor_check",
        ),
        CheckConstraint("pressure_events >= 0", name="curator_trigger_pressure_check"),
        CheckConstraint(
            "last_run_pressure >= 0 AND last_run_pressure <= pressure_events",
            name="curator_trigger_pressure_cursor_check",
        ),
    )

    principal_id: Mapped[str] = mapped_column(Text, primary_key=True)
    admitted_writes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_run_writes: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    pressure_events: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    last_run_pressure: Mapped[int] = mapped_column(
        BigInteger, nullable=False, server_default=text("0")
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CuratorRun(Base):
    """One immutable versioned Palace Health Report and pass receipt."""

    __tablename__ = "curator_run"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('writes','manual','injection_pressure','cron')",
            name="curator_run_trigger_check",
        ),
        CheckConstraint("status IN ('completed','failed')", name="curator_run_status_check"),
        CheckConstraint("admitted_writes_snapshot >= 0", name="curator_run_writes_check"),
        CheckConstraint("pressure_snapshot >= 0", name="curator_run_pressure_check"),
        CheckConstraint(
            "verdict_count >= 0 AND queued_count >= 0 AND executed_count >= 0",
            name="curator_run_counts_check",
        ),
        CheckConstraint(
            "(status = 'completed' AND error IS NULL) OR (status = 'failed' AND error IS NOT NULL)",
            name="curator_run_error_shape_check",
        ),
        Index(
            "curator_run_principal_completed_idx",
            "principal_id",
            desc("completed_at"),
            desc("run_uid"),
        ),
    )

    run_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    report_version: Mapped[str] = mapped_column(Text, nullable=False)
    report: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    admitted_writes_snapshot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    pressure_snapshot: Mapped[int] = mapped_column(BigInteger, nullable=False)
    verdict_count: Mapped[int] = mapped_column(Integer, nullable=False)
    queued_count: Mapped[int] = mapped_column(Integer, nullable=False)
    executed_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CuratorFinding(Base):
    """One deterministic diagnostic finding within a Health Report."""

    __tablename__ = "curator_finding"
    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="curator_finding_ordinal_check"),
        CheckConstraint(
            "kind IN ('duplicate','contradiction','stale','slop','keyword')",
            name="curator_finding_kind_check",
        ),
        CheckConstraint("fingerprint ~ '^[0-9a-f]{64}$'", name="curator_finding_fingerprint_check"),
        UniqueConstraint("run_uid", "ordinal"),
    )

    finding_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    run_uid: Mapped[str] = mapped_column(Text, ForeignKey("curator_run.run_uid"), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    memory_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    evidence: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CuratorVerdict(Base):
    """One immutable LLM verdict over one deterministic finding."""

    __tablename__ = "curator_verdict"
    __table_args__ = (
        CheckConstraint(
            "action IN ('keep','merge','contradict','supersede','retire','keyword_repair','split')",
            name="curator_verdict_action_check",
        ),
        UniqueConstraint("finding_uid"),
    )

    verdict_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    finding_uid: Mapped[str] = mapped_column(
        Text, ForeignKey("curator_finding.finding_uid"), nullable=False
    )
    action: Mapped[str] = mapped_column(Text, nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    proposal: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CuratorAction(Base):
    """One append-only deterministic-tool outcome tied back to its finding."""

    __tablename__ = "curator_action"
    __table_args__ = (
        CheckConstraint(
            "outcome IN ('queued','executed','noop','refused')",
            name="curator_action_outcome_check",
        ),
    )

    action_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    verdict_uid: Mapped[str] = mapped_column(
        Text, ForeignKey("curator_verdict.verdict_uid"), nullable=False
    )
    finding_uid: Mapped[str] = mapped_column(
        Text, ForeignKey("curator_finding.finding_uid"), nullable=False
    )
    queue_item_uid: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    detail: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class SymphonyRunResolution(Base):
    """One immutable G11 winner/loser resolution for a Symphony run."""

    __tablename__ = "symphony_run_resolution"
    __table_args__ = (UniqueConstraint("batch_uid"),)

    run_id: Mapped[str] = mapped_column(Text, primary_key=True)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    batch_uid: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    winner_origin_agent: Mapped[str] = mapped_column(Text, nullable=False)
    machine_id: Mapped[str] = mapped_column(Text, nullable=False)
    judged_context: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class Thread(Base):
    """Thread identity and its first-prepare memory snapshot boundary."""

    __tablename__ = "thread"

    id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    machine_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_key: Mapped[str | None] = mapped_column(Text)
    snapshot_ts: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class InjectionEvent(Base):
    """Append-only injection decision and outcome event."""

    __tablename__ = "injection_event"
    __table_args__ = (
        CheckConstraint(
            "shown_as IN ('injected','near_miss','pinned','budget_cut')",
            name="injection_event_shown_as_check",
        ),
        CheckConstraint(
            "actor_class IN ('human','passive')",
            name="injection_event_actor_class_check",
        ),
        UniqueConstraint("event_uid", name="injection_event_event_uid_key"),
        Index("injection_event_injection_id_idx", "injection_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_uid: Mapped[str] = mapped_column(Text, nullable=False)
    injection_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    thread_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    agent_id: Mapped[str] = mapped_column(Text, nullable=False)
    machine_id: Mapped[str] = mapped_column(Text, nullable=False)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    project_key: Mapped[str | None] = mapped_column(Text)
    agent_kind: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'general'"),
    )
    prompt_text: Mapped[str] = mapped_column(Text, nullable=False)
    scorer_version: Mapped[str] = mapped_column(Text, nullable=False)
    memory_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    memory_kind: Mapped[str] = mapped_column(Text, nullable=False)
    features: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    score: Mapped[float] = mapped_column(REAL, nullable=False)
    rank: Mapped[int] = mapped_column(Integer, nullable=False)
    shown_as: Mapped[str] = mapped_column(Text, nullable=False)
    actor_class: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        server_default=text("'human'"),
    )
    outcome: Mapped[str | None] = mapped_column(Text)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class InjectionEventAnnotation(Base):
    """One immutable verification-only classification over an injection event."""

    __tablename__ = "injection_event_annotation"
    __table_args__ = (
        CheckConstraint(
            "kind = 'verification_only'",
            name="injection_event_annotation_kind_check",
        ),
        CheckConstraint(
            "reason = btrim(reason) AND reason <> ''",
            name="injection_event_annotation_reason_check",
        ),
        CheckConstraint(
            "annotator_principal_id = btrim(annotator_principal_id) "
            "AND annotator_principal_id <> ''",
            name="injection_event_annotation_annotator_principal_check",
        ),
        CheckConstraint(
            "annotator_machine_id = btrim(annotator_machine_id) AND annotator_machine_id <> ''",
            name="injection_event_annotation_annotator_machine_check",
        ),
        CheckConstraint(
            "annotator_origin_agent = btrim(annotator_origin_agent) "
            "AND annotator_origin_agent <> ''",
            name="injection_event_annotation_annotator_agent_check",
        ),
    )

    target_event_uid: Mapped[str] = mapped_column(
        Text,
        ForeignKey(
            "injection_event.event_uid",
            name="injection_event_annotation_target_event_uid_fkey",
        ),
        primary_key=True,
    )
    kind: Mapped[str] = mapped_column(Text, nullable=False)
    target_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    target_machine_id: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    annotator_principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    annotator_machine_id: Mapped[str] = mapped_column(Text, nullable=False)
    annotator_origin_agent: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )


class SpendEvent(Base):
    """One append-only receipt line in ADR-024 normal form."""

    __tablename__ = "spend_event"
    __table_args__ = (
        CheckConstraint(
            "product_type IN ("
            "'llm.request','llm.embedding','llm.fee','infra.db.instance',"
            "'infra.db.storage','infra.run.serve','infra.run.job',"
            "'infra.observability','net.egress','fleet.lease','fleet.snapshot'"
            ") OR product_type LIKE 'ext.api._%'",
            name="spend_event_product_type_check",
        ),
        CheckConstraint(
            "quantity_type = btrim(quantity_type) AND quantity_type <> ''",
            name="spend_event_quantity_type_nonblank_check",
        ),
        CheckConstraint(
            "unit_of_measure = btrim(unit_of_measure) AND unit_of_measure <> ''",
            name="spend_event_unit_of_measure_nonblank_check",
        ),
        CheckConstraint("quantity > 0", name="spend_event_quantity_check"),
        CheckConstraint(
            "cost_usd IS NULL OR cost_usd >= 0",
            name="spend_event_cost_usd_check",
        ),
        CheckConstraint(
            "basis IN ('measured','allocated','estimated')",
            name="spend_event_basis_check",
        ),
        CheckConstraint(
            "behavior IN ('variable','fixed','step')",
            name="spend_event_behavior_check",
        ),
        CheckConstraint(
            "purpose IN "
            "('building','extraction','curation','judge','remember','embedding','scout')",
            name="spend_event_purpose_check",
        ),
        CheckConstraint(
            "ref = btrim(ref) AND ref <> ''",
            name="spend_event_ref_nonblank_check",
        ),
        Index("spend_event_ts_idx", "ts"),
        Index("spend_event_ref_idx", "ref"),
        Index("spend_event_thread_id_idx", "thread_id"),
        Index("spend_event_run_id_idx", "run_id"),
        Index("spend_event_memory_id_idx", "memory_id"),
        {"comment": ("LEDGER receipt line: one immutable sentence for one purchased price class.")},
    )

    event_uid: Mapped[str] = mapped_column(
        Text,
        primary_key=True,
        comment="ULID receipt identity; its leading bits carry the purchase timestamp.",
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        comment="When the provider purchase completed, with timezone.",
    )
    product_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="What was bought: in M2A, an LLM request or embedding.",
    )
    quantity_type: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "The price class bought: fresh input, cached input, cache write, output, or reasoning."
        ),
    )
    unit_of_measure: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="The noun attached to quantity; M2A uses tokens.",
    )
    quantity: Mapped[Decimal] = mapped_column(
        Numeric(30, 9),
        nullable=False,
        comment="How many units were bought; receipt lines never pad with zero units.",
    )
    cost_usd: Mapped[Decimal | None] = mapped_column(
        Numeric(20, 12),
        comment="Native broker dollars for this line, NULL when the bill has not supplied a cost.",
    )
    basis: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "Honesty column: measured, allocated, or estimated; allocation is never measurement."
        ),
    )
    behavior: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Cost lever: variable per action, fixed baseline, or step capacity jump.",
    )
    purpose: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment=(
            "Why bought: building, extraction, curation, judge, remember, embedding, or scout."
        ),
    )
    principal_id: Mapped[str | None] = mapped_column(
        Text,
        comment="Human funding lineage, when known.",
    )
    machine_id: Mapped[str | None] = mapped_column(
        Text,
        comment="Machine lineage, when known.",
    )
    origin_agent: Mapped[str | None] = mapped_column(
        Text,
        comment="Agent path lineage; prefixes roll up a sub-agent subtree.",
    )
    thread_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        comment="Conversation grain; GROUP BY this column is thread cost.",
    )
    run_id: Mapped[str | None] = mapped_column(
        Text,
        comment="Run grain; GROUP BY this column is run cost.",
    )
    prompt_id: Mapped[str | None] = mapped_column(
        Text,
        comment="Query grain shared by its model and embedding purchases.",
    )
    memory_id: Mapped[UUID | None] = mapped_column(
        PGUUID(as_uuid=True),
        comment="Memory grain enabling future cost-per-citation economics.",
    )
    model: Mapped[str | None] = mapped_column(
        Text,
        comment="Executable model identity reported for the purchase.",
    )
    provider: Mapped[str | None] = mapped_column(
        Text,
        comment="Downstream inference provider when known; otherwise the direct provider.",
    )
    quantization: Mapped[str | None] = mapped_column(
        Text,
        comment="Endpoint precision when the broker reports it; NULL is honest unknown.",
    )
    ref: Mapped[str] = mapped_column(
        Text,
        nullable=False,
        comment="Provider response or generation id joining every price class of one request.",
    )
    meta: Mapped[dict[str, Any]] = mapped_column(
        JSONB,
        nullable=False,
        server_default=text("'{}'::jsonb"),
        comment="Replay-safe provider detail and allocation provenance not promoted to a column.",
    )


class SpendReconciliation(Base):
    """One immutable comparison of broker usage with the spend ledger."""

    __tablename__ = "spend_reconciliation"
    __table_args__ = (
        CheckConstraint("provider = 'openrouter'", name="spend_reconciliation_provider_check"),
        CheckConstraint(
            "status IN ('baseline','balanced','drift','unavailable')",
            name="spend_reconciliation_status_check",
        ),
        CheckConstraint("tolerance_usd >= 0", name="spend_reconciliation_tolerance_check"),
        CheckConstraint("unpriced_lines >= 0", name="spend_reconciliation_unpriced_check"),
        CheckConstraint(
            "error_code IS NULL OR error_code IN ('broker_unavailable','invalid_broker_response')",
            name="spend_reconciliation_error_check",
        ),
        CheckConstraint(
            "(status = 'unavailable' AND broker_usage_usd IS NULL "
            "AND ledger_cost_usd IS NULL AND broker_since_baseline_usd IS NULL "
            "AND ledger_since_baseline_usd IS NULL AND drift_usd IS NULL "
            "AND error_code IS NOT NULL) OR "
            "(status = 'baseline' AND broker_usage_usd IS NOT NULL "
            "AND ledger_cost_usd IS NOT NULL AND broker_since_baseline_usd = 0 "
            "AND ledger_since_baseline_usd = 0 AND drift_usd = 0 "
            "AND error_code IS NULL) OR "
            "(status IN ('balanced','drift') AND broker_usage_usd IS NOT NULL "
            "AND ledger_cost_usd IS NOT NULL AND broker_since_baseline_usd IS NOT NULL "
            "AND ledger_since_baseline_usd IS NOT NULL AND drift_usd IS NOT NULL "
            "AND error_code IS NULL)",
            name="spend_reconciliation_shape_check",
        ),
        Index("spend_reconciliation_ts_idx", "ts", "event_uid"),
    )

    event_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )
    provider: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(Text, nullable=False)
    broker_usage_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    ledger_cost_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    broker_since_baseline_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    ledger_since_baseline_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    drift_usd: Mapped[Decimal | None] = mapped_column(Numeric(20, 12))
    tolerance_usd: Mapped[Decimal] = mapped_column(Numeric(20, 12), nullable=False)
    unpriced_lines: Mapped[int] = mapped_column(BigInteger, nullable=False)
    error_code: Mapped[str | None] = mapped_column(Text)


class ScorerConfig(Base):
    """Versioned scorer weights and parameters."""

    __tablename__ = "scorer_config"
    __table_args__ = (
        Index(
            "scorer_config_one_active_idx",
            "active",
            unique=True,
            postgresql_where=text("active"),
        ),
    )

    version: Mapped[str] = mapped_column(Text, primary_key=True)
    weights: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=text("now()"),
    )
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))


class LearnerRun(Base):
    """One immutable receipt for an actual manual or background retrain."""

    __tablename__ = "learner_run"
    __table_args__ = (
        CheckConstraint(
            "trigger IN ('manual','background')",
            name="learner_run_trigger_check",
        ),
        CheckConstraint(
            "result IN ('insufficient_data','not_better','proposed')",
            name="learner_run_result_check",
        ),
        CheckConstraint(
            "eligible_dispositions >= 0",
            name="learner_run_eligible_dispositions_check",
        ),
        CheckConstraint(
            "training_dispositions >= 0",
            name="learner_run_training_dispositions_check",
        ),
        CheckConstraint(
            "holdout_dispositions >= 0",
            name="learner_run_holdout_dispositions_check",
        ),
        CheckConstraint("training_pairs >= 0", name="learner_run_training_pairs_check"),
        Index("learner_run_ts_idx", "ts", "run_uid"),
    )

    run_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    trigger: Mapped[str] = mapped_column(Text, nullable=False)
    result: Mapped[str] = mapped_column(Text, nullable=False)
    incumbent_version: Mapped[str] = mapped_column(
        Text,
        ForeignKey("scorer_config.version"),
        nullable=False,
    )
    proposal_version: Mapped[str | None] = mapped_column(
        Text,
        ForeignKey("scorer_config.version"),
    )
    eligible_dispositions: Mapped[int] = mapped_column(BigInteger, nullable=False)
    training_dispositions: Mapped[int] = mapped_column(BigInteger, nullable=False)
    holdout_dispositions: Mapped[int] = mapped_column(BigInteger, nullable=False)
    training_pairs: Mapped[int] = mapped_column(BigInteger, nullable=False)
    source_boundary: Mapped[str | None] = mapped_column(Text)
    incumbent: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    challenger: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class OptimizationRun(Base):
    """One immutable, cross-loop optimization audit record."""

    __tablename__ = "optimization_run"
    __table_args__ = (
        CheckConstraint(
            "loop = btrim(loop) AND loop <> ''",
            name="optimization_run_loop_check",
        ),
        CheckConstraint(
            "trigger_kind = btrim(trigger_kind) AND trigger_kind <> ''",
            name="optimization_run_trigger_kind_check",
        ),
        CheckConstraint(
            "trigger_event_uid = btrim(trigger_event_uid) AND trigger_event_uid <> ''",
            name="optimization_run_trigger_event_check",
        ),
        CheckConstraint(
            "corpus_fingerprint ~ '^[0-9a-f]{64}$'",
            name="optimization_run_corpus_fingerprint_check",
        ),
        CheckConstraint("corpus_size >= 0", name="optimization_run_corpus_size_check"),
        CheckConstraint("corpus_max_size > 0", name="optimization_run_corpus_max_size_check"),
        CheckConstraint(
            "jsonb_typeof(corpus_stratification) = 'object'",
            name="optimization_run_stratification_check",
        ),
        CheckConstraint(
            "jsonb_typeof(incumbent_params) = 'object'",
            name="optimization_run_incumbent_params_check",
        ),
        CheckConstraint(
            "challenger_params IS NULL OR jsonb_typeof(challenger_params) = 'object'",
            name="optimization_run_challenger_params_check",
        ),
        CheckConstraint(
            "jsonb_typeof(backtest_scores) = 'object'",
            name="optimization_run_backtest_scores_check",
        ),
        CheckConstraint(
            "verdict = btrim(verdict) AND verdict <> ''",
            name="optimization_run_verdict_check",
        ),
        CheckConstraint(
            "jsonb_typeof(tie_break_applied) = 'object'",
            name="optimization_run_tie_break_check",
        ),
        CheckConstraint(
            "jsonb_typeof(cost_refs) = 'array'",
            name="optimization_run_cost_refs_check",
        ),
        CheckConstraint(
            "corpus_size <= corpus_max_size",
            name="optimization_run_corpus_bound_check",
        ),
        CheckConstraint(
            "completed_at >= started_at",
            name="optimization_run_time_span_check",
        ),
        Index("optimization_run_loop_completed_idx", "loop", "completed_at", "run_uid"),
        Index(
            "optimization_run_trigger_thread_idx",
            "trigger_thread_id",
            "completed_at",
            postgresql_where=text("trigger_thread_id IS NOT NULL"),
        ),
    )

    run_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    loop: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_kind: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_event_uid: Mapped[str] = mapped_column(Text, nullable=False)
    trigger_thread_id: Mapped[UUID | None] = mapped_column(PGUUID(as_uuid=True))
    corpus_fingerprint: Mapped[str] = mapped_column(Text, nullable=False)
    corpus_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    corpus_max_size: Mapped[int] = mapped_column(BigInteger, nullable=False)
    corpus_stratification: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    incumbent_version: Mapped[str] = mapped_column(
        Text, ForeignKey("scorer_config.version"), nullable=False
    )
    challenger_version: Mapped[str | None] = mapped_column(
        Text, ForeignKey("scorer_config.version")
    )
    incumbent_params: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    challenger_params: Mapped[dict[str, Any] | None] = mapped_column(JSONB(none_as_null=True))
    backtest_scores: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    verdict: Mapped[str] = mapped_column(Text, nullable=False)
    tie_break_applied: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    cost_refs: Mapped[list[str]] = mapped_column(
        JSONB, nullable=False, server_default=text("'[]'::jsonb")
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ScorerActivation(Base):
    """Append-only authority log for active scorer version changes."""

    __tablename__ = "scorer_activation"
    __table_args__ = (
        CheckConstraint(
            "actor_class IN ('human','passive')",
            name="scorer_activation_actor_class_check",
        ),
        CheckConstraint(
            "reason IN ('human_control','learner_proposal','contract_migration')",
            name="scorer_activation_reason_check",
        ),
        Index("scorer_activation_ts_idx", "ts", "event_uid"),
    )

    event_uid: Mapped[str] = mapped_column(Text, primary_key=True)
    version: Mapped[str] = mapped_column(Text, ForeignKey("scorer_config.version"), nullable=False)
    previous_version: Mapped[str] = mapped_column(
        Text, ForeignKey("scorer_config.version"), nullable=False
    )
    actor_class: Mapped[str] = mapped_column(Text, nullable=False)
    machine_id: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    changes: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class OptimizationRunAdoption(Base):
    """The later owner tap for an immutable optimization proposal."""

    __tablename__ = "optimization_run_adoption"

    run_uid: Mapped[str] = mapped_column(
        Text, ForeignKey("optimization_run.run_uid"), primary_key=True
    )
    tap_event_uid: Mapped[str] = mapped_column(
        Text, ForeignKey("scorer_activation.event_uid"), nullable=False
    )
    adopted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class TranscriptRecord(Base):
    """One immutable, exact line from an owner's local conversation journal."""

    __tablename__ = "transcript_record"
    __table_args__ = (
        CheckConstraint(
            "principal_id = btrim(principal_id) AND principal_id <> ''",
            name="transcript_record_principal_check",
        ),
        CheckConstraint("sequence > 0", name="transcript_record_sequence_check"),
        CheckConstraint("sha256 ~ '^[0-9a-f]{64}$'", name="transcript_record_sha256_check"),
        Index("transcript_record_received_at_idx", "principal_id", "received_at"),
    )

    principal_id: Mapped[str] = mapped_column(Text, primary_key=True)
    thread_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), primary_key=True)
    sequence: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    journal_line: Mapped[str] = mapped_column(Text, nullable=False)
    sha256: Mapped[str] = mapped_column(Text, nullable=False)
    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=text("now()")
    )


class CreationOutcome(Base):
    """Append-only projection; historical injection IDs may outlive memory heads."""

    __tablename__ = "creation_outcome"
    __table_args__ = (Index("creation_outcome_principal_ts", "principal_id", "ts"),)

    event_key: Mapped[str] = mapped_column(Text, primary_key=True)
    memory_id: Mapped[UUID] = mapped_column(PGUUID(as_uuid=True), nullable=False)
    principal_id: Mapped[str] = mapped_column(Text, nullable=False)
    machine_id: Mapped[str] = mapped_column(Text, nullable=False)
    source: Mapped[str] = mapped_column(Text, nullable=False)
    outcome: Mapped[str] = mapped_column(Text, nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
