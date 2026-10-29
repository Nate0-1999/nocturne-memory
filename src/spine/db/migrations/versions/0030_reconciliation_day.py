"""M6SP/A-080: each reconciliation also records today's bill and today's ledger total."""

from alembic import op

revision = "0030"
down_revision = "0029"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    ALTER TABLE spend_reconciliation
      ADD COLUMN broker_day_usd NUMERIC(20,12)
        CONSTRAINT spend_reconciliation_broker_day_check CHECK (broker_day_usd >= 0),
      ADD COLUMN ledger_day_usd NUMERIC(20,12)
        CONSTRAINT spend_reconciliation_ledger_day_check CHECK (ledger_day_usd >= 0)
    """)


def downgrade() -> None:
    op.execute(
        "ALTER TABLE spend_reconciliation DROP COLUMN broker_day_usd, DROP COLUMN ledger_day_usd"
    )
