"""A function may ask a pod connector call to present as the app; nobody else may.

The presenter's half -- the app or a refusal, never the person in its place --
is in ``test_github_presenter.py``; the route, end to end, in
``test_pod_connector_act_as_app_e2e.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import cast
from uuid import uuid4

import pytest

from app.core.authorization.actor import Workload, WorkloadKind
from app.core.authorization.context import Context
from app.modules.connectors.domain.app_identity import (
    AppIdentityRefusedError,
    require_function_run,
)

pytestmark = pytest.mark.asyncio


def _ctx(workload: Workload | None) -> Context:
    """Only ``workload`` is read; the rest of a context does not matter here."""
    return cast(Context, SimpleNamespace(workload=workload))


def _workload(principal_type: str) -> Workload:
    return Workload(
        kind=WorkloadKind.of(principal_type),
        workload_id=uuid4(),
        for_user_id=uuid4(),
        principal_type=principal_type,
    )


async def test_a_function_run_may_ask() -> None:
    require_function_run(_ctx(_workload("function")))


@pytest.mark.parametrize(
    "who",
    [
        pytest.param(None, id="a person, an app's page or the CLI"),
        pytest.param(_workload("agent"), id="an agent's token"),
        # Folded into FUNCTION by `WorkloadKind.of`, which counts anything
        # that is not an agent as a function: the widest identity there is
        # must not go to the next kind of workload by that default.
        pytest.param(_workload("workflow"), id="a workflow's token"),
    ],
)
async def test_anyone_else_is_refused(who: Workload | None) -> None:
    """An agent already acts as the app through its tool; a person pressing a
    button in an app posts as themselves."""
    with pytest.raises(AppIdentityRefusedError) as refused:
        require_function_run(_ctx(who))

    assert refused.value.code == "APP_IDENTITY_FUNCTIONS_ONLY"
    assert refused.value.status_code == 403
