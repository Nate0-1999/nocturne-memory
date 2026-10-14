"""M5LN/F212: manual retraining outlives its initiating request."""

from alembic import op

revision = "0029"
down_revision = "0028"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE retrain_job (
      job_id text PRIMARY KEY,
      result jsonb,
      error text,
      created_at timestamptz NOT NULL DEFAULT now()
    )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE retrain_job")
