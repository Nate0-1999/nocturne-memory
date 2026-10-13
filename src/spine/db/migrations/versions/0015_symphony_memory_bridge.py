"""Add run-scoped Symphony staging and grouped winner consent.

Revision ID: 0015
Revises: 0014
Create Date: 2026-08-17
"""

from collections.abc import Sequence

from alembic import op

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_constraint("memory_unit_status_check", "memory_unit", type_="check")
    op.execute("ALTER TABLE memory_unit ADD COLUMN run_id TEXT")
    op.execute("ALTER TABLE memory_unit ADD COLUMN origin_agent TEXT")
    op.create_check_constraint(
        "memory_unit_status_check",
        "memory_unit",
        "status IN ('active','candidate','staged','quarantined','tombstoned')",
    )
    op.create_check_constraint(
        "memory_unit_run_lineage_pair_check",
        "memory_unit",
        "(run_id IS NULL) = (origin_agent IS NULL)",
    )
    op.create_check_constraint(
        "memory_unit_staged_lineage_check",
        "memory_unit",
        "status <> 'staged' OR run_id IS NOT NULL",
    )
    op.create_index(
        "memory_unit_principal_run_origin_status_idx",
        "memory_unit",
        ["principal_id", "run_id", "origin_agent", "status"],
    )

    op.drop_constraint(
        "approval_queue_item_birthplace_shape_check", "approval_queue_item", type_="check"
    )
    op.drop_constraint(
        "approval_queue_item_birthplace_check", "approval_queue_item", type_="check"
    )
    op.execute("ALTER TABLE approval_queue_item ADD COLUMN birthplace_run_id TEXT")
    op.execute("ALTER TABLE approval_queue_item ADD COLUMN birthplace_origin_agent TEXT")
    op.execute("ALTER TABLE approval_queue_item ADD COLUMN judged_context JSONB")
    op.create_check_constraint(
        "approval_queue_item_birthplace_check",
        "approval_queue_item",
        "birthplace IN ('thread','seed','symphony')",
    )
    op.create_check_constraint(
        "approval_queue_item_birthplace_shape_check",
        "approval_queue_item",
        """
        (birthplace = 'thread' AND birthplace_thread_id IS NOT NULL
          AND batch_uid IS NULL AND source_name IS NULL AND source_sha256 IS NULL
          AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL
          AND judged_context IS NULL)
        OR
        (birthplace = 'seed' AND birthplace_thread_id IS NULL
          AND batch_uid IS NOT NULL AND source_name IS NOT NULL AND source_sha256 IS NOT NULL
          AND birthplace_run_id IS NULL AND birthplace_origin_agent IS NULL
          AND judged_context IS NULL)
        OR
        (birthplace = 'symphony' AND birthplace_thread_id IS NULL
          AND batch_uid IS NOT NULL AND source_name IS NULL AND source_sha256 IS NULL
          AND birthplace_run_id IS NOT NULL AND birthplace_origin_agent IS NOT NULL
          AND judged_context IS NOT NULL)
        """,
    )

    op.execute(
        """
        CREATE TABLE symphony_run_resolution (
          run_id TEXT PRIMARY KEY,
          principal_id TEXT NOT NULL,
          batch_uid UUID NOT NULL UNIQUE,
          winner_origin_agent TEXT NOT NULL,
          machine_id TEXT NOT NULL,
          judged_context JSONB NOT NULL,
          created_at TIMESTAMPTZ NOT NULL DEFAULT now()
        )
        """
    )
    op.execute(
        """
        CREATE FUNCTION reject_symphony_run_resolution_mutation()
        RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
          RAISE EXCEPTION 'symphony_run_resolution is append-only';
        END;
        $$
        """
    )
    op.execute(
        """
        CREATE TRIGGER symphony_run_resolution_no_update
        BEFORE UPDATE ON symphony_run_resolution
        FOR EACH ROW EXECUTE FUNCTION reject_symphony_run_resolution_mutation()
        """
    )
    op.execute(
        """
        CREATE TRIGGER symphony_run_resolution_no_delete
        BEFORE DELETE ON symphony_run_resolution
        FOR EACH ROW EXECUTE FUNCTION reject_symphony_run_resolution_mutation()
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER symphony_run_resolution_no_delete ON symphony_run_resolution")
    op.execute("DROP TRIGGER symphony_run_resolution_no_update ON symphony_run_resolution")
    op.execute("DROP FUNCTION reject_symphony_run_resolution_mutation()")
    op.execute("DROP TABLE symphony_run_resolution")

    op.drop_constraint(
        "approval_queue_item_birthplace_shape_check", "approval_queue_item", type_="check"
    )
    op.drop_constraint(
        "approval_queue_item_birthplace_check", "approval_queue_item", type_="check"
    )
    op.execute("ALTER TABLE approval_queue_item DROP COLUMN judged_context")
    op.execute("ALTER TABLE approval_queue_item DROP COLUMN birthplace_origin_agent")
    op.execute("ALTER TABLE approval_queue_item DROP COLUMN birthplace_run_id")
    op.create_check_constraint(
        "approval_queue_item_birthplace_check",
        "approval_queue_item",
        "birthplace IN ('thread','seed')",
    )
    op.create_check_constraint(
        "approval_queue_item_birthplace_shape_check",
        "approval_queue_item",
        """
        (birthplace = 'thread' AND birthplace_thread_id IS NOT NULL
          AND batch_uid IS NULL AND source_name IS NULL AND source_sha256 IS NULL)
        OR
        (birthplace = 'seed' AND birthplace_thread_id IS NULL
          AND batch_uid IS NOT NULL AND source_name IS NOT NULL AND source_sha256 IS NOT NULL)
        """,
    )

    op.drop_index("memory_unit_principal_run_origin_status_idx", table_name="memory_unit")
    op.drop_constraint("memory_unit_staged_lineage_check", "memory_unit", type_="check")
    op.drop_constraint("memory_unit_run_lineage_pair_check", "memory_unit", type_="check")
    op.drop_constraint("memory_unit_status_check", "memory_unit", type_="check")
    op.execute("ALTER TABLE memory_unit DROP COLUMN origin_agent")
    op.execute("ALTER TABLE memory_unit DROP COLUMN run_id")
    op.create_check_constraint(
        "memory_unit_status_check",
        "memory_unit",
        "status IN ('active','candidate','quarantined','tombstoned')",
    )
