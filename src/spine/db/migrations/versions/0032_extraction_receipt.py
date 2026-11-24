"""A-096: retain each admission decision across replay and review."""

from alembic import op

revision = "0032"
down_revision = "0031"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE extraction_receipt (
        principal_id text NOT NULL,
        candidate_id uuid NOT NULL,
        request_hash text NOT NULL,
        result jsonb NOT NULL,
        PRIMARY KEY (principal_id, candidate_id)
    )
    """)


def downgrade() -> None:
    op.execute("DROP TABLE extraction_receipt")
