"""M3SJ: durable workflow recipes, schedules and execution receipts."""

from alembic import op

revision = "0025"
down_revision = "0024"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE workflow_job (
      job_id text PRIMARY KEY,
      principal_id text NOT NULL,
      machine_id text NOT NULL,
      definition jsonb NOT NULL,
      revision integer NOT NULL DEFAULT 1,
      enabled boolean NOT NULL DEFAULT true,
      next_run_at timestamptz,
      trigger_cursor text,
      created_at timestamptz NOT NULL DEFAULT now()
    )
    """)
    op.execute("CREATE INDEX workflow_job_owner ON workflow_job(principal_id, machine_id)")
    op.execute("""
    CREATE TABLE workflow_run (
      run_id text PRIMARY KEY,
      job_id text NOT NULL REFERENCES workflow_job,
      thread_id uuid NOT NULL UNIQUE,
      trigger_key text NOT NULL,
      definition jsonb NOT NULL,
      state text NOT NULL CHECK
        (state IN ('running','completed','failed','cancelled','interrupted')),
      verdict text,
      started_at timestamptz NOT NULL DEFAULT now(),
      finished_at timestamptz,
      UNIQUE (job_id, trigger_key)
    );
    """)


def downgrade() -> None:
    op.execute("DROP TABLE workflow_run")
    op.execute("DROP TABLE workflow_job")
