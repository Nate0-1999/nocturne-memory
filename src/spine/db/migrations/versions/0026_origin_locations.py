"""M3PL (F146): keep every source folder of a memory that spans several."""

from alembic import op

revision = "0026"
down_revision = "0025"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE memory_unit ADD COLUMN origin_locations text[] NOT NULL DEFAULT '{}'")


def downgrade() -> None:
    op.execute("ALTER TABLE memory_unit DROP COLUMN origin_locations")
