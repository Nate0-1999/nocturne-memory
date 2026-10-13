"""v2.133: a compaction's approved facts trigger a curator pass."""

from alembic import op

revision = "0028"
down_revision = "0027"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE curator_run DROP CONSTRAINT curator_run_trigger_check")
    op.execute(
        "ALTER TABLE curator_run ADD CONSTRAINT curator_run_trigger_check CHECK "
        "(trigger IN ('writes','manual','injection_pressure','cron','compaction'))"
    )


def downgrade() -> None:
    op.execute("ALTER TABLE curator_run DROP CONSTRAINT curator_run_trigger_check")
    op.execute(
        "ALTER TABLE curator_run ADD CONSTRAINT curator_run_trigger_check CHECK "
        "(trigger IN ('writes','manual','injection_pressure','cron'))"
    )
