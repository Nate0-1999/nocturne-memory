"""A-028 live-Postgres proofs for the Palace Vitals read contract."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

from conftest import basis_vector
from fastapi import FastAPI
from httpx import ASGITransport, AsyncClient
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spine.db.models import MemoryUnit
from spine.spend.views import SpendViewRefresher
from spine.vitals.service import _spend_snapshot


def _event(
    seed: int,
    *,
    timestamp: datetime,
    purpose: str,
    model: str | None,
    provider: str,
    cost_usd: str | None,
) -> dict[str, object]:
    return {
        "event_uid": f"00000000000000000000000{seed:03d}",
        "ts": timestamp.isoformat(),
        "product_type": "llm.request",
        "quantity_type": "output",
        "unit_of_measure": "tokens",
        "quantity": "1",
        "cost_usd": cost_usd,
        "basis": "measured",
        "behavior": "variable",
        "purpose": purpose,
        "principal_id": "owner",
        "machine_id": "machine-vitals",
        "origin_agent": "harness-chat",
        "model": model,
        "provider": provider,
        "ref": f"vitals-ref-{seed}",
        "meta": {},
    }


async def test_vitals_measures_database_size_without_guessing_harness_resources(
    memory_client: AsyncClient,
) -> None:
    """A-044 keeps database observation in Spine and local process values unavailable."""
    response = await memory_client.get("/v1/vitals?principal_id=local&scope=palace")

    assert response.status_code == 200
    resources = response.json()["resources"]
    assert resources["status"] == "partial"
    assert resources["database_bytes"] > 0
    assert resources["daemon_rss_bytes"] is None
    assert resources["daemon_uptime_seconds"] is None
    assert resources["disk_free_bytes"] is None
    assert resources["disk_total_bytes"] is None
    assert resources["journal_bytes"] is None
    assert resources["backup_bytes"] is None
    assert resources["warning"] is None


async def _insert_memory_heads(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    anchor: datetime,
) -> None:
    rows = (
        (UUID(int=8101), "Recent active pin", "active", True, anchor - timedelta(minutes=15)),
        (
            UUID(int=8102),
            "Recent quarantined pin",
            "quarantined",
            True,
            anchor - timedelta(minutes=20),
        ),
        (UUID(int=8103), "Old active", "active", False, anchor - timedelta(minutes=61)),
    )
    async with session_factory() as session:
        async with session.begin():
            for memory_id, label, status, pin, created_at in rows:
                session.add(
                    MemoryUnit(
                        id=memory_id,
                        principal_id="owner",
                        label=label,
                        body=f"{label} body",
                        kind="fact",
                        keywords=["vitals"],
                        embedding=basis_vector(memory_id.int % 3),
                        embedding_model="test-embedding-1536",
                        project_key=None,
                        thread_origin=None,
                        origin_path=None,
                        pin=pin,
                        status=status,
                        revision=1,
                        created_at=created_at,
                        updated_at=created_at,
                    )
                )


def test_spend_snapshot_escapes_reserved_and_tilde_model_keys_without_merging() -> None:
    """A-028 is defended by verifying that spend snapshot escapes reserved and tilde model keys
    without merging; this prevents drift in the honest canonical Vitals projection.
    """
    minute = datetime(2026, 8, 2, 12, tzinfo=UTC)
    snapshot = _spend_snapshot(
        [
            {
                "minute": minute,
                "purpose": "building",
                "model": None,
                "receipt_lines": 2,
                "cost_usd": Decimal("0.400000000000"),
                "unpriced_lines": 1,
            },
            {
                "minute": minute,
                "purpose": "judge",
                "model": "unreported",
                "receipt_lines": 1,
                "cost_usd": Decimal("0.600000000000"),
                "unpriced_lines": 0,
            },
            {
                "minute": minute,
                "purpose": "judge",
                "model": "~unreported",
                "receipt_lines": 1,
                "cost_usd": Decimal("0.700000000000"),
                "unpriced_lines": 0,
            },
        ]
    )

    model_lanes = [lane for lane in snapshot.lanes if lane.dimension == "model"]
    assert len({lane.key for lane in model_lanes}) == len(model_lanes)
    assert [
        (lane.key, lane.label, lane.points[0].cost_usd, lane.points[0].unpriced_lines)
        for lane in model_lanes
    ] == [
        ("unreported", "Model not reported", "0.400000000000", 1),
        ("~unreported", "unreported", "0.600000000000", 0),
        ("~~unreported", "~unreported", "0.700000000000", 0),
    ]

    total_point = snapshot.lanes[0].points[0]
    assert total_point.cost_usd == "1.700000000000"
    for dimension in ("purpose", "model"):
        dimension_points = [
            point for lane in snapshot.lanes if lane.dimension == dimension for point in lane.points
        ]
        assert sum(point.receipt_lines for point in dimension_points) == total_point.receipt_lines
        assert sum(point.unpriced_lines for point in dimension_points) == total_point.unpriced_lines
        assert sum(
            Decimal(point.cost_usd) for point in dimension_points if point.cost_usd is not None
        ) == Decimal(total_point.cost_usd)


async def test_vitals_snapshot_is_canonical_conserving_and_honest(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-028 is defended by verifying that vitals snapshot is canonical conserving and honest;
    this prevents drift in the honest canonical Vitals projection.
    """
    async with memory_session_factory() as session:
        anchor = await session.scalar(select(func.date_trunc("minute", func.now())))
    assert isinstance(anchor, datetime) and anchor.tzinfo is not None

    early = anchor - timedelta(minutes=30)
    events = [
        _event(
            1,
            timestamp=early,
            purpose="building",
            model="model-alpha",
            provider="provider-a",
            cost_usd="1.100000000000",
        ),
        _event(
            2,
            timestamp=early,
            purpose="building",
            model="model-alpha",
            provider="provider-b",
            cost_usd="0.200000000000",
        ),
        _event(
            3,
            timestamp=early,
            purpose="judge",
            model="model-beta",
            provider="provider-a",
            cost_usd=None,
        ),
        _event(
            4,
            timestamp=anchor,
            purpose="building",
            model=None,
            provider="provider-a",
            cost_usd="0.400000000000",
        ),
        _event(
            5,
            timestamp=anchor - timedelta(minutes=61),
            purpose="building",
            model="outside-window",
            provider="provider-a",
            cost_usd="99.000000000000",
        ),
    ]
    inserted = await memory_client.post("/v1/spend/events", json={"events": events})
    assert inserted.status_code == 200
    await _insert_memory_heads(memory_session_factory, anchor=anchor)
    await SpendViewRefresher(memory_session_factory).refresh_once()

    response = await memory_client.get("/v1/vitals?principal_id=local&scope=palace")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("application/json")
    snapshot = response.json()
    assert snapshot["window_minutes"] == 60
    assert snapshot["spend"]["source_view"] == "v_spend_rate"
    assert datetime.fromisoformat(snapshot["as_of"]).tzinfo is not None
    assert datetime.fromisoformat(snapshot["spend"]["latest_minute"]) == anchor

    lanes = snapshot["spend"]["lanes"]
    assert [(lane["dimension"], lane["key"], lane["label"]) for lane in lanes] == [
        ("total", None, "All spend"),
        ("purpose", "building", "building"),
        ("purpose", "judge", "judge"),
        ("model", "model-alpha", "model-alpha"),
        ("model", "model-beta", "model-beta"),
        ("model", "unreported", "Model not reported"),
    ]
    total_points = lanes[0]["points"]
    assert [datetime.fromisoformat(point["minute"]) for point in total_points] == [early, anchor]
    assert [
        {key: value for key, value in point.items() if key != "minute"} for point in total_points
    ] == [
        {
            "cost_usd": "1.300000000000",
            "receipt_lines": 3,
            "unpriced_lines": 1,
        },
        {
            "cost_usd": "0.400000000000",
            "receipt_lines": 1,
            "unpriced_lines": 0,
        },
    ]
    assert lanes[2]["points"][0]["cost_usd"] is None
    assert lanes[2]["points"][0]["unpriced_lines"] == 1
    assert all("outside-window" != lane["key"] for lane in lanes)
    for dimension in ("total", "purpose", "model"):
        dimension_points = [
            point for lane in lanes if lane["dimension"] == dimension for point in lane["points"]
        ]
        assert sum(point["receipt_lines"] for point in dimension_points) == 4
        assert sum(point["unpriced_lines"] for point in dimension_points) == 1
        assert sum(
            Decimal(point["cost_usd"])
            for point in dimension_points
            if point["cost_usd"] is not None
        ) == Decimal("1.700000000000")
    assert snapshot["lifecycle_rates"] == [
        {
            "metric": "created",
            "status": "measured",
            "per_hour": 2,
            "source": "memory_unit.created_at",
        },
        *[
            {"metric": metric, "status": "not_recorded", "per_hour": None, "source": None}
            for metric in (
                "reinforced",
                "superseded",
                "merged",
                "quarantined",
                "tombstoned",
                "add_backs",
            )
        ],
    ]
    assert snapshot["palace_counts"] == [
        {
            "metric": "active_units",
            "status": "measured",
            "count": 2,
            "source": "memory_unit.status",
        },
        {
            "metric": "pinned_units",
            "status": "measured",
            "count": 1,
            "source": "memory_unit.status+pin",
        },
        {
            "metric": "candidates_pending",
            "status": "measured",
            "count": 0,
            "source": "memory_unit.status",
        },
        {
            "metric": "edges",
            "status": "measured",
            "count": 0,
            "source": "memory_edge",
        },
        {"metric": "staged_units", "status": "not_recorded", "count": None, "source": None},
        {
            "metric": "queue_depth",
            "status": "measured",
            "count": 0,
            "source": "approval_queue_item.state",
        },
    ]


async def test_thread_vitals_reads_only_authoritative_receipts_for_that_thread(
    memory_client: AsyncClient,
) -> None:
    """A-028 is defended by verifying that thread vitals reads only authoritative receipts for
    that thread; this prevents drift in the honest canonical Vitals projection.
    """
    now = datetime.now(UTC).replace(second=0, microsecond=0)
    selected = UUID(int=8801)
    other = UUID(int=8802)
    selected_event = _event(
        81,
        timestamp=now,
        purpose="building",
        model="model-current",
        provider="provider-a",
        cost_usd="0.250000000000",
    )
    selected_event["thread_id"] = str(selected)
    other_event = _event(
        82,
        timestamp=now,
        purpose="judge",
        model="model-other",
        provider="provider-a",
        cost_usd="8.000000000000",
    )
    other_event["thread_id"] = str(other)
    inserted = await memory_client.post(
        "/v1/spend/events", json={"events": [selected_event, other_event]}
    )
    assert inserted.status_code == 200

    response = await memory_client.get(
        f"/v1/vitals/threads/{selected}?principal_id=local&scope=palace"
    )

    assert response.status_code == 200
    spend = response.json()["spend"]
    assert spend["source_view"] == "spend_event"
    assert spend["lanes"][0]["points"][0]["cost_usd"] == "0.250000000000"
    assert all(lane["key"] != "judge" for lane in spend["lanes"])


async def test_vitals_has_an_empty_total_lane_and_rejects_query_parameters(
    memory_client: AsyncClient,
    memory_session_factory: async_sessionmaker[AsyncSession],
) -> None:
    """A-028 is defended by verifying that vitals has an empty total lane and rejects query
    parameters; this prevents drift in the honest canonical Vitals projection.
    """
    await SpendViewRefresher(memory_session_factory).refresh_once()
    live = await memory_client.get("/v1/vitals?principal_id=local&scope=palace")
    rejected = await memory_client.get("/v1/vitals?principal_id=local&window_minutes=30")

    assert live.status_code == 200
    assert live.json()["spend"] == {
        "source_view": "v_spend_rate",
        "latest_minute": None,
        "lanes": [
            {
                "dimension": "total",
                "key": None,
                "label": "All spend",
                "points": [],
            }
        ],
    }
    assert rejected.status_code == 422
    assert rejected.headers["content-type"].startswith("application/problem+json")
    assert rejected.json()["endpoint"] == "GET /v1/vitals"


async def test_vitals_requires_the_service_bearer(memory_app: FastAPI) -> None:
    """A-028 is defended by verifying that vitals requires the service bearer; this prevents
    drift in the honest canonical Vitals projection.
    """
    async with AsyncClient(
        transport=ASGITransport(app=memory_app),
        base_url="http://test",
    ) as client:
        response = await client.get("/v1/vitals")

    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.headers["content-type"].startswith("application/problem+json")
    assert response.json()["endpoint"] == "GET /v1/vitals"
