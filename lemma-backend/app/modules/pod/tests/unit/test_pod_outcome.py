"""What a pod owns: a bar held every week, or a finish line by a date."""

from __future__ import annotations

from datetime import date, datetime, timezone
from uuid import UUID, uuid4

import pytest
from pydantic import ValidationError

from app.core.ports.outcome_measures import MeasureReading
from app.modules.pod.contracts.members import say_what_it_owns
from app.modules.pod.domain.errors import PodConflictError
from app.modules.pod.domain.pod_entities import (
    OutcomeInput,
    PodConfig,
    PodEntity,
    PodJoinPolicy,
    PodOutcome,
)
from app.modules.pod.services.pod_outcome_service import (
    PodOutcomeService,
    same_query,
)

MEASURE = (
    "SELECT round(100.0 * count(*) FILTER (WHERE answered_in_time) / "
    "nullif(count(*), 0), 1) FROM conversations"
)
BAR = {
    "sentence": "Every customer hears back within a day.",
    "kind": "week",
    "target": 90,
    "unit": "%",
    "measure": MEASURE,
}
FINISH = {
    "sentence": "The launch ships on November 18.",
    "kind": "date",
    "target": 12,
    "unit": "steps",
    "due": "2026-11-18",
    "measure": "SELECT count(*) FROM steps WHERE done",
}


def test_the_plan_files_front_matter_reads_straight_across():
    outcome = OutcomeInput.model_validate(BAR)
    assert (outcome.kind.value, outcome.target, outcome.unit) == ("week", 90, "%")
    assert OutcomeInput.model_validate(FINISH).due == date(2026, 11, 18)


def test_a_finish_line_needs_its_date_and_a_bar_has_none():
    with pytest.raises(ValidationError, match="needs its due date"):
        OutcomeInput.model_validate({**FINISH, "due": None})
    with pytest.raises(ValidationError, match="has no due date"):
        OutcomeInput.model_validate({**BAR, "due": "2026-11-18"})


def test_a_share_is_held_to_a_percentage_not_a_fraction():
    assert OutcomeInput.model_validate({**BAR, "target": 0.9}).target == 0.9
    with pytest.raises(ValidationError, match="from 0 to 100"):
        OutcomeInput.model_validate({**BAR, "target": 900})


def test_blank_measure_and_unit_are_unset_and_the_sentence_is_one_line():
    outcome = OutcomeInput.model_validate(
        {
            "sentence": "  Every invoice\n is paid within 30 days. ",
            "kind": "week",
            "measure": "   ",
            "unit": "",
        }
    )
    assert outcome.sentence == "Every invoice is paid within 30 days."
    assert outcome.measure is None and outcome.unit is None


def test_a_config_without_an_outcome_stays_as_it_was():
    assert "outcome" not in PodConfig().model_dump(mode="json")
    stored = PodConfig.model_validate({"outcome": BAR}).model_dump(mode="json")
    assert stored["outcome"]["kind"] == "week"


def test_an_outcome_stored_in_an_older_shape_reads_as_none_not_a_reset_config():
    config = PodConfig.from_raw(
        {
            "join_policy": "ORG_MEMBERS",
            "outcome": {"sentence": "Ship it.", "kind": "bar", "measure": {"x": 1}},
        }
    )
    assert config.outcome is None
    assert config.join_policy.value == "ORG_MEMBERS"


def test_the_agent_is_told_what_it_owns_and_where_the_plan_is():
    said = say_what_it_owns({"outcome": BAR})
    assert said is not None
    assert said.startswith("Every customer hears back within a day.")
    assert "every week" in said and "90%" in said and "/plan.md" in said
    finish = say_what_it_owns({"outcome": FINISH})
    assert finish is not None and "due 2026-11-18" in finish and "12 steps" in finish
    unmeasured = say_what_it_owns({"outcome": {**BAR, "measure": None}})
    assert unmeasured is not None and "set_what_you_own" in unmeasured
    assert say_what_it_owns({}) is None


def test_any_edit_to_a_measure_is_a_new_measure():
    assert same_query("SELECT 1 FROM t\n", "SELECT 1 FROM t")
    assert not same_query("SELECT 'a  b' FROM t", "SELECT 'a b' FROM t")
    assert not same_query("SELECT 1 FROM t", "SELECT 2 FROM t")
    assert same_query(None, None) and not same_query(None, "SELECT 1 FROM t")


class _Pods:
    def __init__(self, pod: PodEntity) -> None:
        self.pod = pod

    async def lock(self, id: UUID) -> None:
        del id

    async def get(self, id: UUID) -> PodEntity | None:
        return self.pod if id == self.pod.id else None

    async def update(self, entity: PodEntity) -> PodEntity:
        self.pod = entity
        return entity


class _Editor:
    """A context that may update the pod, and nothing is asked of it beyond."""

    async def require(self, permission: object, resource: object) -> None:
        del permission, resource


class _Measures:
    """Answers every check with ``value``, or refuses every one."""

    def __init__(self, *, value: float | None = 87.5, refuse: bool = False) -> None:
        self.value = value
        self.refuse = refuse

    async def check(self, *, pod_id: UUID, sql: str, ctx: object) -> MeasureReading:
        del pod_id, sql, ctx
        if self.refuse:
            raise PermissionError("cannot read every row it counts")
        return MeasureReading(
            value=self.value, measured_at=datetime(2026, 10, 21, tzinfo=timezone.utc)
        )


def _service(outcome: PodOutcome | None = None) -> tuple[PodOutcomeService, _Pods]:
    pod = PodEntity(
        id=uuid4(),
        name="Support",
        organization_id=uuid4(),
        user_id=uuid4(),
        config=PodConfig(outcome=outcome),
    )
    pods = _Pods(pod)
    return PodOutcomeService(pods, None, today=lambda: date(2026, 10, 21)), pods


@pytest.mark.asyncio
async def test_a_new_measure_is_read_before_it_is_kept_and_answers_its_value():
    service, pods = _service()
    kept, reading = await service.set_outcome(
        pods.pod.id, OutcomeInput.model_validate(BAR), _Editor(), measures=_Measures()
    )
    assert reading is not None and reading.value == 87.5
    assert kept is not None and kept.since == date(2026, 10, 21)
    assert pods.pod.config.outcome == kept


@pytest.mark.asyncio
async def test_a_refused_measure_is_not_kept():
    service, pods = _service()
    with pytest.raises(PermissionError):
        await service.set_outcome(
            pods.pod.id,
            OutcomeInput.model_validate(BAR),
            _Editor(),
            measures=_Measures(refuse=True),
        )
    assert pods.pod.config.outcome is None


@pytest.mark.asyncio
async def test_moving_the_target_needs_no_right_to_read_every_row():
    before = PodOutcome.model_validate({**BAR, "since": "2026-09-01"})
    service, pods = _service(before)
    kept, reading = await service.set_outcome(
        pods.pod.id,
        OutcomeInput.model_validate({**BAR, "target": 95, "measure": MEASURE + "\n"}),
        _Editor(),
        measures=_Measures(refuse=True),
    )
    assert reading is None
    assert kept is not None and kept.target == 95
    assert kept.since == date(2026, 9, 1), "kept through a new target"


@pytest.mark.asyncio
async def test_becoming_the_other_kind_of_thing_starts_its_days_again():
    before = PodOutcome.model_validate({**BAR, "since": "2026-09-01"})
    service, pods = _service(before)
    kept, _ = await service.set_outcome(
        pods.pod.id,
        OutcomeInput.model_validate(FINISH),
        _Editor(),
        measures=_Measures(),
    )
    assert kept is not None and kept.since == date(2026, 10, 21)


@pytest.mark.asyncio
async def test_letting_it_go_clears_only_the_outcome():
    service, pods = _service(PodOutcome.model_validate(BAR))
    pods.pod = pods.pod.model_copy(
        update={
            "config": pods.pod.config.model_copy(
                update={"join_policy": PodJoinPolicy.ORG_MEMBERS}
            )
        }
    )
    kept, reading = await service.set_outcome(
        pods.pod.id, None, _Editor(), measures=_Measures(refuse=True)
    )
    assert (kept, reading) == (None, None)
    assert pods.pod.config.outcome is None
    assert pods.pod.config.join_policy is PodJoinPolicy.ORG_MEMBERS


@pytest.mark.asyncio
async def test_a_starting_outcome_keeps_a_templates_measure_without_reading_it():
    service, pods = _service()
    kept = await service.set_starting_outcome(
        pods.pod.id, OutcomeInput.model_validate(FINISH), _Editor()
    )
    assert kept.measure == FINISH["measure"]
    assert pods.pod.config.outcome == kept
    with pytest.raises(PodConflictError):
        await service.set_starting_outcome(
            pods.pod.id, OutcomeInput.model_validate(BAR), _Editor()
        )
