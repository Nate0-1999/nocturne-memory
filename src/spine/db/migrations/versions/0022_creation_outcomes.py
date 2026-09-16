"""Append replayable creation outcomes from existing authoritative writes."""

from alembic import op

revision = "0022"
down_revision = "0021"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("""
    CREATE TABLE creation_outcome (
      event_key TEXT PRIMARY KEY,
      memory_id UUID NOT NULL,
      principal_id TEXT NOT NULL,
      machine_id TEXT NOT NULL,
      source TEXT NOT NULL,
      outcome TEXT NOT NULL,
      reason TEXT NOT NULL,
      ts TIMESTAMPTZ NOT NULL
    )
    """)
    op.execute("CREATE INDEX creation_outcome_principal_ts ON creation_outcome(principal_id, ts)")
    op.execute("""
    CREATE TRIGGER creation_outcome_append_only BEFORE UPDATE OR DELETE ON creation_outcome
      FOR EACH ROW EXECUTE FUNCTION optimization_history_reject_mutation();
    """)
    op.execute("""
    CREATE FUNCTION creation_source(target UUID) RETURNS TEXT LANGUAGE sql STABLE AS $$
      SELECT CASE WHEN editor = 'compaction' THEN 'compaction'
        WHEN editor = 'extraction' THEN 'extraction'
        WHEN editor IN ('user','human') OR reason LIKE 'remember/%' THEN 'remember'
        WHEN editor LIKE 'seed%' OR reason LIKE 'seed%' THEN 'seed'
        WHEN reason LIKE 'curation/%' THEN 'curator'
        WHEN editor LIKE 'agent%' THEN 'agent-save' ELSE 'unknown' END
      FROM memory_revision WHERE memory_id = target ORDER BY ts, rev_uid LIMIT 1
    $$;
    """)
    op.execute("""
    CREATE FUNCTION capture_creation_revision(r memory_revision) RETURNS void
    LANGUAGE plpgsql AS $$
    DECLARE outcome_kind TEXT;
    BEGIN
      outcome_kind := CASE
        WHEN r.revision = 1 THEN 'created'
        WHEN r.reason LIKE 'panel/delete%' THEN 'deleted'
        WHEN r.reason = 'rejected' OR r.reason LIKE 'curation/%/rejected' THEN 'rejected'
        WHEN r.reason LIKE 'curation/%/retire' THEN 'curator_retired'
        WHEN r.reason = 'approved' THEN 'admitted'
        ELSE NULL END;
      IF outcome_kind IS NOT NULL THEN
        INSERT INTO creation_outcome
          SELECT 'revision:' || r.rev_uid, r.memory_id, m.principal_id,
            r.origin_machine_id, coalesce(creation_source(r.memory_id),'unknown'),
            outcome_kind, r.reason, r.ts FROM memory_unit m WHERE m.id = r.memory_id
          ON CONFLICT DO NOTHING;
      END IF;
    END $$;
    """)
    op.execute("""
    CREATE FUNCTION creation_revision_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN PERFORM capture_creation_revision(NEW); RETURN NEW; END $$;
    """)
    op.execute("""
    CREATE TRIGGER creation_revision AFTER INSERT ON memory_revision
      FOR EACH ROW EXECUTE FUNCTION creation_revision_trigger();
    """)
    op.execute("""
    SELECT capture_creation_revision(r) FROM memory_revision r ORDER BY r.ts, r.rev_uid;
    """)
    op.execute("""
    CREATE FUNCTION capture_creation_injection(e injection_event) RETURNS void
    LANGUAGE plpgsql AS $$
    BEGIN
      IF e.outcome = 'removed:never' OR
         e.outcome IN ('kept','cited','added_back','auto_entered','mid_thread_added') THEN
        INSERT INTO creation_outcome VALUES (
          'injection:' || e.event_uid, e.memory_id, e.principal_id, e.machine_id,
          coalesce(creation_source(e.memory_id),'unknown'),
          CASE WHEN e.outcome = 'removed:never' THEN 'never' ELSE 'used' END,
          e.outcome, e.ts) ON CONFLICT DO NOTHING;
      END IF;
    END $$;
    """)
    op.execute("""
    CREATE FUNCTION creation_injection_trigger() RETURNS trigger LANGUAGE plpgsql AS $$
    BEGIN PERFORM capture_creation_injection(NEW); RETURN NEW; END $$;
    """)
    op.execute("""
    CREATE TRIGGER creation_injection AFTER INSERT ON injection_event
      FOR EACH ROW EXECUTE FUNCTION creation_injection_trigger();
    """)
    op.execute("""
    SELECT capture_creation_injection(e) FROM injection_event e ORDER BY e.ts, e.event_uid;
    """)


def downgrade() -> None:
    for statement in (
        "DROP TRIGGER creation_revision ON memory_revision",
        "DROP TRIGGER creation_injection ON injection_event",
        "DROP FUNCTION creation_revision_trigger()",
        "DROP FUNCTION creation_injection_trigger()",
        "DROP FUNCTION capture_creation_revision(memory_revision)",
        "DROP FUNCTION capture_creation_injection(injection_event)",
        "DROP FUNCTION creation_source(UUID)",
        "DROP TABLE creation_outcome",
    ):
        op.execute(statement)
