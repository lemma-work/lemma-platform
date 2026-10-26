"""A run's resolved runtime reaches the harness as one typed value.

It used to ride in ``options.extra["runtime_profile"]`` as a dict: each reader
re-checked it with ``isinstance``, compared its protocol to a string literal,
and the Agent Host dispatch evaluated ``config["harness_snapshot_revision"]``
only so that a missing revision would raise ``KeyError``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.agent.agent_host.domain.host import AgentHostRunState
from app.modules.agent.domain.harness_options import HarnessOptions
from app.modules.agent.domain.runtime_profiles import (
    RuntimeProfileProtocol,
    RuntimeProfileScope,
)
from app.modules.agent.domain.runtime_snapshot import RuntimeSnapshot
from app.modules.agent.agent_host.harness.run_config import (
    agent_host_run_config,
)
from app.modules.agent.agent_host.harness.run_window import (
    RunLease,
    lease_terminal_detail,
)
from app.modules.agent.services.run_usage_recorder import metered_per_request

pytestmark = pytest.mark.unit


def _host_snapshot(config: dict[str, object]) -> RuntimeSnapshot:
    return RuntimeSnapshot(
        profile_id=str(uuid4()),
        profile_name="Laptop",
        harness_id=str(uuid4()),
        scope=RuntimeProfileScope.PERSONAL,
        protocol=RuntimeProfileProtocol.AGENT_HOST,
        provider_model_name=" opus ",
        config=config,
    )


def test_dispatch_reads_the_harness_settings_from_the_typed_snapshot() -> None:
    snapshot = _host_snapshot(
        {
            "harness_snapshot_revision": "rev-3",
            "config_selections": {"mode": "plan"},
            "host_wait_timeout_seconds": 90,
        }
    )

    run = agent_host_run_config(
        HarnessOptions(model_name="x", runtime_profile=snapshot)
    )

    assert str(run.harness_id) == snapshot.harness_id
    assert (run.config_selections, run.wait_timeout_seconds) == ({"mode": "plan"}, 90)
    assert run.model_name == "opus"


def test_a_snapshot_with_no_revision_names_what_is_missing() -> None:
    options = HarnessOptions(model_name="x", runtime_profile=_host_snapshot({}))

    with pytest.raises(ValueError, match="harness_snapshot_revision"):
        agent_host_run_config(options)


def test_a_run_with_no_snapshot_is_refused_before_dispatch() -> None:
    with pytest.raises(ValueError, match="runtime profile is missing"):
        agent_host_run_config(HarnessOptions(model_name="x"))


@pytest.mark.parametrize(
    ("protocol", "metered"),
    [
        (RuntimeProfileProtocol.OPENAI_COMPATIBLE.value, True),
        (RuntimeProfileProtocol.ANTHROPIC_COMPATIBLE.value, True),
        (RuntimeProfileProtocol.AGENT_HOST.value, False),
        ("SOMETHING_RETIRED", False),
        (None, False),
    ],
)
def test_metering_reads_the_protocol_as_the_enum(
    protocol: str | None, metered: bool
) -> None:
    assert metered_per_request({"protocol": protocol}) is metered
    assert metered_per_request(None) is False


def test_the_lease_is_read_once_into_typed_fields() -> None:
    accepted = datetime.now(timezone.utc)
    row = SimpleNamespace(
        state=AgentHostRunState.FAILED.value,
        accepted_at=accepted,
        error_detail="  The computer refused the run.  ",
    )

    lease = RunLease.of(row)  # type: ignore[arg-type]  # TST-03: the three columns read

    assert lease == RunLease(
        state=AgentHostRunState.FAILED,
        accepted_at=accepted,
        error_detail="  The computer refused the run.  ",
    )
    assert lease_terminal_detail(lease) == "The computer refused the run."
    assert RunLease.of(None) is None
