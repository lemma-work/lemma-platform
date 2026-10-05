"""A run's title: validated on save, rendered on read, stored in `start`."""

import pytest
from pydantic import ValidationError

from app.modules.workflow.api.schemas import (
    WorkflowCreateRequest,
    WorkflowUpdateRequest,
)
from app.modules.workflow.domain.context import RunContext
from app.modules.workflow.domain.run_title import (
    MAX_RUN_TITLE_LENGTH,
    render_run_title,
    validate_run_title,
)
from app.modules.workflow.infrastructure.start_column import join_start, split_start


def _view(**nodes) -> dict:
    return RunContext(nodes=nodes).to_view()


# -- rendering ----------------------------------------------------------------


def test_parts_are_joined_in_order():
    view = _view(collect={"name": "Priya Shah", "role": "Designer"})
    assert (
        render_run_title(["collect.name", "collect.role"], view)
        == "Priya Shah · Designer"
    )


def test_a_part_not_filled_in_yet_is_skipped_not_printed_as_none():
    view = _view(collect={"name": "Priya Shah"})
    assert render_run_title(["collect.name", "review.verdict"], view) == "Priya Shah"


def test_nothing_filled_in_is_no_title():
    assert render_run_title(["collect.name"], _view()) is None


def test_scalars_are_said_plainly_and_containers_skipped():
    view = _view(c={"amount": 9400.0, "urgent": True, "tags": ["a"], "who": "  A  B "})
    assert (
        render_run_title(["c.amount", "c.urgent", "c.tags", "c.who"], view)
        == "9400 · yes · A B"
    )


def test_a_long_title_is_cut_to_one_line():
    view = _view(c={"text": "x" * 500})
    title = render_run_title(["c.text"], view)
    assert title is not None
    assert len(title) == MAX_RUN_TITLE_LENGTH
    assert title.endswith("…")


def test_trigger_payload_is_reachable():
    view = RunContext.model_validate(
        {"start": {"payload": {"ticket": "OPS-12"}}}
    ).to_view()
    assert render_run_title(["start.payload.ticket"], view) == "OPS-12"


# -- validation ----------------------------------------------------------------


def test_validation_refuses_a_bad_expression():
    with pytest.raises(ValueError, match="run_title"):
        validate_run_title(["collect.[name"])


def test_validation_refuses_more_than_four_parts():
    with pytest.raises(ValueError, match="at most 4"):
        validate_run_title(["a", "b", "c", "d", "e"])


def test_request_models_return_422_shapes_for_bad_titles():
    with pytest.raises(ValidationError):
        WorkflowCreateRequest(name="hire", run_title=["collect.[name"])
    with pytest.raises(ValidationError):
        WorkflowUpdateRequest(run_title=[""])
    assert WorkflowUpdateRequest(run_title=[]).run_title == []
    assert "run_title" not in WorkflowUpdateRequest().model_fields_set


# -- storage --------------------------------------------------------------------


def test_title_rides_inside_start_and_comes_back_out():
    start = {"type": "MANUAL", "config": None}
    stored = join_start(start, ["collect.name"])
    assert stored == {"type": "MANUAL", "config": None, "run_title": ["collect.name"]}
    assert split_start(stored) == (start, ["collect.name"])


def test_a_title_without_a_trigger_reads_back_as_no_trigger():
    stored = join_start(None, ["collect.name"])
    assert stored == {"run_title": ["collect.name"]}
    assert split_start(stored) == (None, ["collect.name"])


def test_no_title_stores_exactly_what_was_stored_before():
    assert join_start(None, []) is None
    assert join_start({"type": "MANUAL"}, []) == {"type": "MANUAL"}
    assert split_start(None) == (None, [])
    assert split_start({"type": "MANUAL"}) == ({"type": "MANUAL"}, [])


def test_a_malformed_stored_title_is_dropped_not_raised():
    assert split_start({"type": "MANUAL", "run_title": "collect.name"}) == (
        {"type": "MANUAL"},
        [],
    )
    assert split_start({"type": "MANUAL", "run_title": ["a", 3, " "]}) == (
        {"type": "MANUAL"},
        ["a"],
    )


def test_blank_parts_are_refused_and_kept_parts_are_stored_stripped():
    with pytest.raises(ValueError, match="must not be empty"):
        validate_run_title(["   "])
    assert validate_run_title(["  collect.name "]) == ["collect.name"]
