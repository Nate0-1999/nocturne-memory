"""A-102: count completed plan turns outside the monetary lenses."""

from alembic import op

revision = "0033"
down_revision = "0032"
branch_labels = None
depends_on = None


def _product_check(coverage: bool) -> None:
    previous = """product_type IN (
        'llm.request','llm.embedding','llm.fee','infra.db.instance','infra.db.storage',
        'infra.run.serve','infra.run.job','infra.observability','net.egress',
        'fleet.lease','fleet.snapshot') OR product_type LIKE 'ext.api._%'"""
    op.drop_constraint("spend_event_product_type_check", "spend_event")
    op.create_check_constraint(
        "spend_event_product_type_check",
        "spend_event",
        previous + (" OR product_type = 'subscription.turn'" if coverage else ""),
    )


def _cost_views(source: str) -> None:
    previous = "spend_event" if source == "metered_spend_event" else "metered_spend_event"
    # Preserve the shipped column order, aggregation and comments in both directions.
    op.execute(rf"""
        DO $$
        DECLARE view_name text; definition text; description text;
        BEGIN
            FOREACH view_name IN ARRAY ARRAY[
                'v_spend_rate', 'v_thread_cost', 'v_run_cost', 'v_memory_cost'
            ] LOOP
                SELECT pg_get_viewdef(view_name::regclass),
                       obj_description(view_name::regclass, 'pg_class')
                  INTO definition, description;
                EXECUTE format('DROP MATERIALIZED VIEW %I', view_name);
                EXECUTE format('CREATE MATERIALIZED VIEW %I AS %s', view_name,
                    regexp_replace(definition, '\m{previous}\M', '{source}', 'g'));
                EXECUTE format('COMMENT ON MATERIALIZED VIEW %I IS %L', view_name, description);
            END LOOP;
        END $$
    """)


def upgrade() -> None:
    _product_check(True)
    op.create_check_constraint(
        "spend_event_coverage_check",
        "spend_event",
        """
        product_type <> 'subscription.turn' OR (
            quantity_type = 'turn' AND unit_of_measure = 'turns' AND quantity = 1
            AND cost_usd IS NULL AND basis = 'measured' AND behavior = 'fixed'
            AND purpose = 'building' AND principal_id IS NOT NULL AND thread_id IS NOT NULL
            AND run_id IS NOT NULL AND prompt_id IS NOT NULL AND model IS NOT NULL
            AND provider IS NOT NULL AND provider IN ('codex','claudeAgent')
            AND model ~ '^[A-Za-z0-9][A-Za-z0-9._/-]*$'
            AND meta ? 'account' AND jsonb_typeof(meta->'account') = 'string'
            AND meta->>'account' ~ '^[0-9a-f]{64}$' AND meta - 'account' = '{}'::jsonb
        )""",
    )
    op.execute("""CREATE UNIQUE INDEX spend_event_covered_run_idx ON spend_event
        (principal_id, run_id) WHERE product_type = 'subscription.turn'""")
    op.execute("""CREATE VIEW metered_spend_event AS
        SELECT * FROM spend_event WHERE product_type <> 'subscription.turn'""")
    _cost_views("metered_spend_event")


def downgrade() -> None:
    # Restoring the older constraint refuses a downgrade that would lose coverage records.
    _product_check(False)
    _cost_views("spend_event")
    op.drop_index("spend_event_covered_run_idx", table_name="spend_event")
    op.execute("DROP VIEW metered_spend_event")
    op.drop_constraint("spend_event_coverage_check", "spend_event")
