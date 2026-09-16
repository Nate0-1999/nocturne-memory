"""Capture creation outcomes when the reviewed gate updates its injection rows."""

from alembic import op

revision = "0023"
down_revision = "0022"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE OR REPLACE FUNCTION capture_creation_injection(e injection_event) RETURNS void
    LANGUAGE plpgsql AS $$
    BEGIN
      IF e.outcome = 'removed:never' OR
         e.outcome IN ('kept','cited','added_back','auto_entered','mid_thread_added') THEN
        -- Keep legacy entries; do not append the same fact again during backfill.
        IF EXISTS (SELECT 1 FROM creation_outcome
          WHERE event_key = 'injection:' || e.event_uid AND reason = e.outcome) THEN
          RETURN;
        END IF;
        INSERT INTO creation_outcome VALUES (
          'injection:' || e.event_uid || ':' || e.outcome,
          e.memory_id, e.principal_id, e.machine_id,
          coalesce(creation_source(e.memory_id),'unknown'),
          CASE WHEN e.outcome = 'removed:never' THEN 'never' ELSE 'used' END,
          e.outcome, e.ts) ON CONFLICT DO NOTHING;
      END IF;
    END $$;
    """)
    op.execute("""
    CREATE OR REPLACE FUNCTION creation_injection_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN
      -- AFTER-trigger local NEW is only the projection input, never a history rewrite.
      IF TG_OP = 'UPDATE' THEN NEW.ts := clock_timestamp(); END IF;
      PERFORM capture_creation_injection(NEW);
      RETURN NEW;
    END $$;
    """)
    op.execute("DROP TRIGGER creation_injection ON injection_event")
    op.execute("""
    CREATE TRIGGER creation_injection AFTER INSERT OR UPDATE OF outcome ON injection_event
      FOR EACH ROW EXECUTE FUNCTION creation_injection_trigger();
    """)
    # Preserve the best-known original event time for decisions predating this migration.
    op.execute("""
    SELECT capture_creation_injection(e) FROM injection_event e ORDER BY e.ts, e.event_uid;
    """)


def downgrade() -> None:
    op.execute("DROP TRIGGER creation_injection ON injection_event")
    op.execute("""
    CREATE TRIGGER creation_injection AFTER INSERT ON injection_event
      FOR EACH ROW EXECUTE FUNCTION creation_injection_trigger();
    """)
