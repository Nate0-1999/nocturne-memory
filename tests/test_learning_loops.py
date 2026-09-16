"""M3LL acceptance over disposable Postgres and frozen scorer fixtures."""

from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import UUID

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError
from test_m2k_api import _insert_graph_fixture

from spine.db.models import InjectionEvent, MemoryRevision, ScorerConfig
from spine.ids import mint_ulid
from spine.inject.axes import AxisNomination, apply_axes
from spine.inject.scorer import ScorerConfig as RuntimeConfig
from spine.inject.scorer import ScorerParams, ScorerWeights
from spine.learner.creation import creation_snapshot, sweep_unused
from spine.learner.model import FEATURE_NAMES, FitSettings, LearningExample, fit_project_offsets
from spine.learner.service import LearnerService, LearnerSettings


async def test_creation_stream_replays_reasons_and_excludes_verification(memory_session_factory):
    """A-067: authoritative writes append outcomes; hygiene never trains verification."""
    first, second = await _insert_graph_fixture(memory_session_factory)
    async with memory_session_factory() as session, session.begin():
        await sweep_unused(session)
        session.add(
            MemoryRevision(
                rev_uid=mint_ulid(),
                memory_id=first,
                revision=3,
                body="First graph memory",
                label="Graph one",
                editor="user",
                origin_machine_id="studio",
                reason="panel/delete/should_never_have_been_saved",
            )
        )
        await session.flush()
        snapshot = await creation_snapshot(session, "owner")
        source = next(row for row in snapshot["sources"] if row["source"] == "remember")
        assert (source["created"], source["surviving"]) == (2, 1)
        assert any(
            row["reason"].endswith("should_never_have_been_saved") for row in snapshot["events"]
        )
        session.add(
            MemoryRevision(
                rev_uid=mint_ulid(),
                memory_id=second,
                revision=2,
                body="Second graph memory",
                label="Graph two",
                editor="user",
                origin_machine_id="nocturne-verification-machine",
                reason="panel/delete/no_longer_needed",
            )
        )
        await session.flush()
        snapshot = await creation_snapshot(session, "owner")
        assert snapshot["hygiene_excluded_events"] >= 2
        assert all(row["memory_id"] != second for row in snapshot["events"])
    async with memory_session_factory() as session:
        with pytest.raises(DBAPIError):
            await session.execute(text("UPDATE creation_outcome SET reason='rewritten'"))


def test_project_offsets_pool_toward_globals_and_unseen_projects_stay_zero():
    """A-067 / SPEC D.2 144: opposite project evidence produces shrunk residuals after globals."""
    examples = []
    for gate, project in enumerate(("orchard", "workshop"), 1):
        for index, target in enumerate((True, False)):
            semantic = float(target if project == "orchard" else not target)
            examples.append(
                LearningExample(
                    event_uid=f"{gate}-{index}",
                    injection_id=UUID(int=gate),
                    memory_id=UUID(int=gate * 10 + index),
                    ts=datetime.now(UTC),
                    features=(semantic, 1 - semantic, 0, 0, 0, 0),
                    baseline_bias=0,
                    target_injected=target,
                    actor_weight=Decimal(1),
                    shown_as="injected",
                    body_tokens=10,
                    project_key=project,
                )
            )
    weights = (0.5, 0.5, 0, 0, 0, 0)

    def fit(penalty):
        return fit_project_offsets(
            examples,
            weights=weights,
            biases={},
            thread_weight=0,
            where_weight=0,
            settings=FitSettings(pair_margin=0.8, bias_l2=penalty),
        )

    weak, strong = fit(1), fit(100)
    assert weak["orchard"]["sem"] > 0 > weak["workshop"]["sem"]
    assert abs(strong["orchard"]["sem"]) < abs(weak["orchard"]["sem"])
    assert "unseen" not in weak
    assert all(abs(sum(offset.values())) < 1e-10 for offset in weak.values())
    forced = RuntimeConfig(
        version="manual-global-change",
        weights=ScorerWeights(sem=0, kw=1, time=0, proj=0, freq=0, hist=0),
        params=ScorerParams(
            tau=0.5,
            near_miss_k=1,
            memory_context_share=0.1,
            half_life_time_days=30,
            half_life_hist_days=30,
            candidate_pool=100,
        ),
        project_offsets=weak,
    )
    assert forced.weights_for_project("unseen") is forced.weights
    for project in weak:
        effective = forced.weights_for_project(project)
        values = [getattr(effective, name) for name in FEATURE_NAMES]
        assert min(values) >= 0
        assert sum(values) == pytest.approx(1)


@pytest.mark.parametrize("retire", [False, True])
async def test_curator_axis_proposal_activation_and_zero_weight_replay(
    memory_session_factory,
    memory_client,
    retire,
):
    """A-067 / SPEC D.2 130: fixture log → nomination → proposal → tap; retirement is zero."""
    async with memory_session_factory() as session, session.begin():
        active = await session.scalar(select(ScorerConfig).where(ScorerConfig.active))
        params = deepcopy(active.params)
        params["tau"] = 0.45
        weights = dict.fromkeys(FEATURE_NAMES, 0.0)
        weights.update(sem=1.0 if retire else 0.5, kw=0.0 if retire else 0.5)
        if retire:
            params["axes"] = {
                "sem_and_kw": AxisNomination(
                    name="sem_and_kw",
                    label="Meaning × Keywords",
                    inputs=("sem", "kw"),
                    weight=1e-7,
                    action="axis_add",
                    rationale="Prior proposal",
                    provenance={"zero_generations": 1},
                ).model_dump(mode="json")
            }
        active.active = False
        session.add(
            ScorerConfig(version="m3ll-fixture", weights=weights, params=params, active=True)
        )
    for gate in range(16):
        async with memory_session_factory() as session, session.begin():
            features = [(1.0, 1.0), (0.0, 0.0)] if retire else [(1.0, 1.0), (1.0, 0.0), (0.0, 1.0)]
            for index, (semantic, keyword) in enumerate(features):
                positive = index == 0
                session.add(
                    InjectionEvent(
                        event_uid=f"ll-{gate}-{index}",
                        injection_id=UUID(int=1000 + gate),
                        thread_id=UUID(int=2000 + gate),
                        agent_id="general",
                        machine_id="local-fixture-corpus",
                        principal_id="fixture-owner",
                        project_key=None,
                        agent_kind="general",
                        prompt_text="fixture",
                        scorer_version="m3ll-fixture",
                        memory_id=UUID(int=3000 + gate * 3 + index),
                        memory_kind="fact",
                        features={
                            **dict.fromkeys(FEATURE_NAMES, 0.0),
                            "sem": semantic,
                            "kw": keyword,
                            "_memory": {"body": "frozen fixture"},
                        },
                        score=semantic if retire else (semantic + keyword) / 2,
                        rank=index + 1,
                        shown_as="injected" if positive or not retire else "near_miss",
                        actor_class="human",
                        outcome="kept" if positive else "removed:not_relevant",
                        ts=datetime(2026, 9, 1, tzinfo=UTC) + timedelta(hours=gate),
                    )
                )
    # This DB is a disposable fixture. Explicitly prove production hygiene first;
    # then use neutral LOCAL identities to exercise the learner without bypasses.
    service = LearnerService(
        memory_session_factory,
        settings=LearnerSettings(
            min_dispositions=25,
            holdout_fraction=0.25,
            passive_discount=0.25,
            pair_margin=0.8,
            bias_l2=1.0,
            win_margin=1.0,
        ),
    )
    assert (await service.retrain()).eligible_dispositions == 0
    async with memory_session_factory() as session, session.begin():
        # Reinsert equivalent fixture rows, never relabel append-only evidence.
        rows = (await session.scalars(select(InjectionEvent))).all()
        for row in rows:
            values = {
                column.name: getattr(row, column.name)
                for column in InjectionEvent.__table__.columns
                if column.name != "id"
            }
            values.update(
                event_uid="auth-" + row.event_uid,
                injection_id=UUID(int=row.injection_id.int + 10000),
                principal_id="local-owner",
                machine_id="local-machine",
            )
            session.add(InjectionEvent(**values))
    result = await service.retrain()
    assert result.status == "proposed", result
    async with memory_session_factory() as session:
        proposed = await session.get(ScorerConfig, result.proposal_version)
        assert not proposed.active
        assert proposed.params["_learner"]["settings"]["trainables"]
        axes = RuntimeConfig.from_mappings(
            version=proposed.version, weights=proposed.weights, params=proposed.params
        ).axes
        assert axes
        if retire:
            assert axes["sem_and_kw"].action == "axis_retire"
            assert axes["sem_and_kw"].weight == 0
        else:
            assert any(axis.weight > 0 for axis in axes.values())
        original = 0.6789012345
        for axis in axes.values():
            zero = axis.model_copy(update={"weight": 0.0})
            assert apply_axes(original, {"sem": 0.4, "kw": 0.9}, {axis.name: zero}) == original
            assert apply_axes(original, {}, {axis.name: axis}) == original
    response = await memory_client.post(
        f"/v1/scorer-configs/{result.proposal_version}/activate",
        json={"event_uid": mint_ulid(), "actor_class": "human", "machine_id": "local-machine"},
    )
    assert response.status_code == 200, response.text
    assert response.json()["status"] == "active"
    scoped = await memory_client.post(
        "/v1/scorer-console/query",
        json={"principal_id": "outside-principal", "as_of": "now"},
    )
    assert scoped.status_code == 200, scoped.text
    visible = scoped.json()["configurations"][0]
    assert visible["project_offsets"] == {}
    assert all(
        axis["provenance"] == {"visibility": "owner-only"} for axis in visible["axes"].values()
    )
