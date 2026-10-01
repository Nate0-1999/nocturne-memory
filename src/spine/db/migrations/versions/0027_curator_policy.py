"""M3LF: retain the curator's model policy in its Palace."""

from alembic import op

revision = "0027"
down_revision = "0026"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
        CREATE TABLE curator_model_policy (
            event_uid text PRIMARY KEY,
            principal_id text NOT NULL,
            policy text NOT NULL,
            changed_at timestamptz NOT NULL DEFAULT clock_timestamp()
        )
    """)
    op.execute(
        "CREATE TRIGGER curator_model_policy_append_only "
        "BEFORE UPDATE OR DELETE ON curator_model_policy "
        "FOR EACH ROW EXECUTE FUNCTION nocturne_refuse_curator_history_change()"
    )


def downgrade() -> None:
    op.execute("DROP TABLE curator_model_policy")
