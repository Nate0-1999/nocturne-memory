"""M6LP: expose measured fitting progress before the result commits."""

from alembic import op

revision = "0031"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE retrain_progress (
      job_id text PRIMARY KEY REFERENCES retrain_job(job_id),
      points jsonb NOT NULL
    )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE retrain_progress")
