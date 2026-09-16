"""M3VZ / A-068: persist observable curator transitions before the pass completes."""

from alembic import op

revision = "0024"
down_revision = "0023"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE curator_progress (
      event_id BIGSERIAL PRIMARY KEY,
      principal_id TEXT NOT NULL,
      run_uid TEXT NOT NULL,
      phase TEXT NOT NULL CHECK (phase IN
        ('run.started','finding.started','finding.completed','run.completed','run.failed')),
      memory_ids UUID[] NOT NULL DEFAULT '{}',
      finding_uid TEXT,
      action TEXT,
      ts TIMESTAMPTZ NOT NULL DEFAULT clock_timestamp()
    )
    """)
    op.execute("CREATE INDEX curator_progress_principal_event_idx "
               "ON curator_progress (principal_id, event_id)")
    op.execute("CREATE TRIGGER curator_progress_append_only "
               "BEFORE UPDATE OR DELETE ON curator_progress "
               "FOR EACH ROW EXECUTE FUNCTION nocturne_refuse_curator_history_change()")


def downgrade() -> None:
    op.execute("DROP TABLE curator_progress")
