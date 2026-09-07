"""`PodRole` and core's role names must stay the same four strings.

The normalisers moved to `app/core/authorization/roles.py` so that core stops
importing a module to spell the roles it already owns. The cost of that move is
this: core names the four roles as literals, `mod:pod` names them as an enum,
and nothing in the type system makes those agree. A fifth role added to one
side only is the failure this test exists for -- it would not break an import,
it would quietly make a role that no pod is provisioned with.
"""

from __future__ import annotations

import pytest

from app.core.authorization.roles import (
    ROLE_ALIASES,
    SYSTEM_POD_ROLE_NAMES,
    normalize_role_name,
)
from app.modules.pod.domain.roles import PodRole
from app.modules.pod.domain.visibility import (
    ROLE_HIERARCHY,
    SYSTEM_POD_ROLE_VALUES,
)

pytestmark = pytest.mark.unit


def test_the_enum_and_cores_literals_are_the_same_four_names() -> None:
    assert {role.value for role in PodRole} == set(SYSTEM_POD_ROLE_NAMES)


def test_the_hierarchy_covers_exactly_those_names() -> None:
    # `SYSTEM_POD_ROLE_VALUES` is derived from `ROLE_HIERARCHY`, so a role added
    # to the enum but not ranked would silently drop out of visibility ordering.
    assert set(ROLE_HIERARCHY) == set(SYSTEM_POD_ROLE_NAMES)
    assert SYSTEM_POD_ROLE_VALUES == set(SYSTEM_POD_ROLE_NAMES)


def test_every_alias_resolves_to_a_real_role() -> None:
    assert set(ROLE_ALIASES.values()) <= set(SYSTEM_POD_ROLE_NAMES)


@pytest.mark.parametrize("role", list(PodRole))
def test_normalising_the_enum_yields_its_value(role: PodRole) -> None:
    # The reason core can normalise a `PodRole` without importing one: the enum
    # is a `str` mixin, so `.strip().upper()` runs on the value. `str(role)`
    # would give "PodRole.ADMIN" instead, which is why core calls the method
    # rather than the constructor.
    assert normalize_role_name(role) == role.value
