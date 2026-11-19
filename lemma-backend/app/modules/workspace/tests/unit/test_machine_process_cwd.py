"""Where a process started over `/machines/{ref}/processes` runs.

The same place the file API would name: a relative directory is read from
~/lemma, `~` is the home, and nothing outside the home is accepted.
"""

from __future__ import annotations

from dataclasses import replace
from uuid import uuid4

import pytest

from app.modules.workspace.api.controllers.machine_processes_controller import _cwd
from app.modules.workspace.domain.machine import (
    Capabilities,
    DEFAULT_MACHINE_SIZE,
    Machine,
    MachineBillingScope,
    MachineFabric,
    MachineKind,
    MachineTemplate,
    MachineVisibility,
)
from app.modules.workspace.domain.machine_errors import MachineConflict
from sandbox_runtime.paths import HOME_ROOT, WORKSPACE_ROOT

MACHINE = Machine(
    id=uuid4(),
    kind=MachineKind.DEFAULT,
    name="me",
    display_name="My machine",
    template=MachineTemplate.WORKSPACE,
    size=DEFAULT_MACHINE_SIZE,
    fabric=MachineFabric.DOCKER,
    capabilities=Capabilities(),
    visibility=MachineVisibility.PRIVATE,
    billing_scope=MachineBillingScope.USER,
    owner_id=uuid4(),
)


@pytest.mark.parametrize(
    ("asked", "runs_in"),
    [
        (None, WORKSPACE_ROOT),
        ("", WORKSPACE_ROOT),
        ("qa/site", f"{WORKSPACE_ROOT}/qa/site"),
        ("./qa/../qa", f"{WORKSPACE_ROOT}/qa"),
        ("~", HOME_ROOT),
        ("~/projects/app", f"{HOME_ROOT}/projects/app"),
        (f"{HOME_ROOT}/projects", f"{HOME_ROOT}/projects"),
        ("..", HOME_ROOT),
    ],
)
def test_a_directory_is_read_the_way_the_file_api_reads_a_path(asked, runs_in):
    assert _cwd(MACHINE, asked) == runs_in


@pytest.mark.parametrize("asked", ["/etc", "../..", "~/../root", "/home/userx"])
def test_a_directory_outside_the_home_is_refused(asked):
    with pytest.raises(MachineConflict) as refused:
        _cwd(MACHINE, asked)
    assert refused.value.code == "MACHINE_PATH_OUTSIDE_HOME"


def test_a_connected_machine_reads_from_its_own_home():
    laptop = replace(MACHINE, kind=MachineKind.CONNECTED, home="/Users/ada")
    assert _cwd(laptop, None) == "/Users/ada/lemma"
    assert _cwd(laptop, "src") == "/Users/ada/lemma/src"
    assert _cwd(laptop, "~/code") == "/Users/ada/code"
