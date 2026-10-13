"""Add durable curator diagnostics, verdicts, actions, and consent cards.

Revision ID: 0018
Revises: 0017
Create Date: 2026-08-31
"""

from __future__ import annotations

from collections.abc import Sequence

from alembic import op

revision: str = "0018"
down_revision: str | None = "0017"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.execute(
        """
        CREATE TABLE curator_trigger_state (
          principal_id TEXT PRIMARY KEY,
          admitted_writes BIGINT NOT NULL DEFAULT 0 CHECK (admitted_writes >= 0),
          last_run_writes BIGINT NOT NULL DEFAULT 0
            CHECK (last_run_writes >= 0 AND last_run_writes <= admitted_writes),
          updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        INSERT INTO curator_trigger_state (principal_id, admitted_writes, last_run_writes)
        SELECT principal_id, count(*), count(*)
        FROM memory_unit
        WHERE status = 'active'
        GROUP BY principal_id
        """
    )
    op.execute(
        """
        CREATE FUNCTION nocturne_tick_curator_write() RETURNS trigger AS $$
        BEGIN
          IF NEW.status = 'active'
             AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'active') THEN
            INSERT INTO curator_trigger_state (principal_id, admitted_writes, last_run_writes)
            VALUES (NEW.principal_id, 1, 0)
            ON CONFLICT (principal_id) DO UPDATE
              SET admitted_writes = curator_trigger_state.admitted_writes + 1,
                  updated_at = now();
          END IF;
          RETURN NEW;
        END;
        $$ LANGUAGE plpgsql
        """
    )
    op.execute(
        """
        CREATE TRIGGER memory_unit_curator_write_tick
        AFTER INSERT OR UPDATE OF status ON memory_unit
        FOR EACH ROW EXECUTE FUNCTION nocturne_tick_curator_write()
        """
    )

    op.execute(
        """
        CREATE TABLE curator_run (
          run_uid TEXT PRIMARY KEY,
          principal_id TEXT NOT NULL,
          trigger TEXT NOT NULL CHECK (trigger IN ('writes','manual','injection_pressure','cron')),
          report_version TEXT NOT NULL,
          report JSONB NOT NULL,
          admitted_writes_snapshot BIGINT NOT NULL CHECK (admitted_writes_snapshot >= 0),
          verdict_count INTEGER NOT NULL CHECK (verdict_count >= 0),
          queued_count INTEGER NOT NULL CHECK (queued_count >= 0),
          executed_count INTEGER NOT NULL CHECK (executed_count >= 0),
          status TEXT NOT NULL CHECK (status IN ('completed','failed')),
          error TEXT,
          started_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          completed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          CHECK ((status = 'completed' AND error IS NULL) OR
                 (status = 'failed' AND error IS NOT NULL))
        )
        """
    )
    op.execute(
        "CREATE INDEX curator_run_principal_completed_idx "
        "ON curator_run (principal_id, completed_at DESC, run_uid DESC)"
    )
    op.execute(
        """
        CREATE TABLE curator_finding (
          finding_uid TEXT PRIMARY KEY,
          run_uid TEXT NOT NULL REFERENCES curator_run(run_uid),
          ordinal INTEGER NOT NULL CHECK (ordinal >= 0),
          kind TEXT NOT NULL CHECK (
            kind IN ('duplicate','contradiction','stale','slop','keyword')
          ),
          memory_ids JSONB NOT NULL,
          evidence JSONB NOT NULL,
          fingerprint TEXT NOT NULL CHECK (fingerprint ~ '^[0-9a-f]{64}$'),
          created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
          UNIQUE (run_uid, ordinal)
        )
        """
    )
    op.execute(
        """
        CREATE TABLE curator_verdict (
          verdict_uid TEXT PRIMARY KEY,
          finding_uid TEXT NOT NULL UNIQUE REFERENCES curator_finding(finding_uid),
          action TEXT NOT NULL CHECK (
            action IN ('keep','merge','contradict','supersede','retire','keyword_repair','split')
          ),
          rationale TEXT NOT NULL,
          proposal JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE TABLE curator_action (
          action_uid TEXT PRIMARY KEY,
          verdict_uid TEXT NOT NULL REFERENCES curator_verdict(verdict_uid),
          finding_uid TEXT NOT NULL REFERENCES curator_finding(finding_uid),
          queue_item_uid TEXT,
          outcome TEXT NOT NULL CHECK (outcome IN ('queued','executed','noop','refused')),
          detail JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )

    op.execute(
        "ALTER TABLE approval_queue_item DROP CONSTRAINT "
        "approval_queue_item_candidate_memory_id_key"
    )
    op.execute(
        "CREATE UNIQUE INDEX approval_queue_item_non_curator_candidate_uidx "
        "ON approval_queue_item (candidate_memory_id) WHERE birthplace <> 'curator'"
    )
    op.execute("ALTER TABLE approval_queue_item DROP CONSTRAINT approval_queue_item_verdict_check")
    op.execute(
        "ALTER TABLE approval_queue_item DROP CONSTRAINT "
        "approval_queue_item_birthplace_check"
    )
    op.execute(
        "ALTER TABLE approval_queue_item DROP CONSTRAINT "
        "approval_queue_item_birthplace_shape_check"
    )
    op.execute("ALTER TABLE approval_queue_item ADD COLUMN candidate_revision INTEGER")
    op.execute("ALTER TABLE approval_queue_item ADD COLUMN curator_run_uid TEXT")
    op.execute("ALTER TABLE approval_queue_item ADD COLUMN curator_finding_uid TEXT UNIQUE")
    op.execute("ALTER TABLE approval_queue_item ADD COLUMN proposal_payload JSONB")
    op.execute(
        "ALTER TABLE approval_queue_item ADD CONSTRAINT approval_queue_item_verdict_check "
        "CHECK (verdict IN "
        "('new','merge','supersede','contradict','retire','keyword_repair','split'))"
    )
    op.execute(
        "ALTER TABLE approval_queue_item ADD CONSTRAINT approval_queue_item_birthplace_check "
        "CHECK (birthplace IN ('thread','seed','symphony','curator'))"
    )
    op.execute(
        """
        ALTER TABLE approval_queue_item
        ADD CONSTRAINT approval_queue_item_birthplace_shape_check CHECK (
          (birthplace = 'thread' AND birthplace_thread_id IS NOT NULL
            AND batch_uid IS NULL AND source_name IS NULL AND source_sha256 IS NULL
            AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL
            AND judged_context IS NULL AND curator_run_uid IS NULL
            AND curator_finding_uid IS NULL AND proposal_payload IS NULL
            AND candidate_revision IS NULL) OR
          (birthplace = 'seed' AND birthplace_thread_id IS NULL
            AND batch_uid IS NOT NULL AND source_name IS NOT NULL AND source_sha256 IS NOT NULL
            AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL
            AND judged_context IS NULL AND curator_run_uid IS NULL
            AND curator_finding_uid IS NULL AND proposal_payload IS NULL
            AND candidate_revision IS NULL) OR
          (birthplace = 'symphony' AND birthplace_thread_id IS NULL
            AND batch_uid IS NOT NULL AND source_name IS NULL AND source_sha256 IS NULL
            AND birthplace_run_id IS NOT NULL AND birthplace_origin_agent IS NOT NULL
            AND judged_context IS NOT NULL AND curator_run_uid IS NULL
            AND curator_finding_uid IS NULL AND proposal_payload IS NULL
            AND candidate_revision IS NULL) OR
          (birthplace = 'curator' AND birthplace_thread_id IS NULL
            AND batch_uid IS NULL AND source_name IS NULL AND source_sha256 IS NULL
            AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL
            AND judged_context IS NULL AND curator_run_uid IS NOT NULL
            AND curator_finding_uid IS NOT NULL AND proposal_payload IS NOT NULL
            AND candidate_revision IS NOT NULL)
        )
        """
    )

    op.execute(
        """
        CREATE FUNCTION nocturne_refuse_curator_history_change() RETURNS trigger AS $$
        BEGIN
          RAISE EXCEPTION 'curator history is append-only';
        END;
        $$ LANGUAGE plpgsql
        """
    )
    for table in ("curator_run", "curator_finding", "curator_verdict", "curator_action"):
        op.execute(
            f"CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION nocturne_refuse_curator_history_change()"
        )


def downgrade() -> None:
    for table in ("curator_action", "curator_verdict", "curator_finding", "curator_run"):
        op.execute(f"DROP TRIGGER {table}_append_only ON {table}")
    op.execute("DROP FUNCTION nocturne_refuse_curator_history_change")
    op.execute(
        "ALTER TABLE approval_queue_item DROP CONSTRAINT "
        "approval_queue_item_birthplace_shape_check"
    )
    op.execute(
        "ALTER TABLE approval_queue_item DROP CONSTRAINT "
        "approval_queue_item_birthplace_check"
    )
    op.execute("ALTER TABLE approval_queue_item DROP CONSTRAINT approval_queue_item_verdict_check")
    op.execute("ALTER TABLE approval_queue_item DROP COLUMN proposal_payload")
    op.execute("ALTER TABLE approval_queue_item DROP COLUMN curator_finding_uid")
    op.execute("ALTER TABLE approval_queue_item DROP COLUMN curator_run_uid")
    op.execute("ALTER TABLE approval_queue_item DROP COLUMN candidate_revision")
    op.execute("DROP INDEX approval_queue_item_non_curator_candidate_uidx")
    op.execute(
        "ALTER TABLE approval_queue_item ADD CONSTRAINT "
        "approval_queue_item_candidate_memory_id_key UNIQUE (candidate_memory_id)"
    )
    op.execute(
        "ALTER TABLE approval_queue_item ADD CONSTRAINT approval_queue_item_verdict_check "
        "CHECK (verdict IN ('new','merge','supersede','contradict'))"
    )
    op.execute(
        "ALTER TABLE approval_queue_item ADD CONSTRAINT approval_queue_item_birthplace_check "
        "CHECK (birthplace IN ('thread','seed','symphony'))"
    )
    op.execute(
        """
        ALTER TABLE approval_queue_item
        ADD CONSTRAINT approval_queue_item_birthplace_shape_check CHECK (
          (birthplace = 'thread' AND birthplace_thread_id IS NOT NULL
            AND batch_uid IS NULL AND source_name IS NULL AND source_sha256 IS NULL
            AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL
            AND judged_context IS NULL) OR
          (birthplace = 'seed' AND birthplace_thread_id IS NULL
            AND batch_uid IS NOT NULL AND source_name IS NOT NULL AND source_sha256 IS NOT NULL
            AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL
            AND judged_context IS NULL) OR
          (birthplace = 'symphony' AND birthplace_thread_id IS NULL
            AND batch_uid IS NOT NULL AND source_name IS NULL AND source_sha256 IS NULL
            AND birthplace_run_id IS NOT NULL AND birthplace_origin_agent IS NOT NULL
            AND judged_context IS NOT NULL)
        )
        """
    )
    op.execute("DROP TABLE curator_action")
    op.execute("DROP TABLE curator_verdict")
    op.execute("DROP TABLE curator_finding")
    op.execute("DROP INDEX curator_run_principal_completed_idx")
    op.execute("DROP TABLE curator_run")
    op.execute("DROP TRIGGER memory_unit_curator_write_tick ON memory_unit")
    op.execute("DROP FUNCTION nocturne_tick_curator_write")
    op.execute("DROP TABLE curator_trigger_state")
