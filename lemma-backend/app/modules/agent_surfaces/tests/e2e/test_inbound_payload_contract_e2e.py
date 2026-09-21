"""Hold every inbound payload builder to what the production parser reads.

`test_platform_contracts_e2e.py` pins the outbound half — what Lemma sends each
platform. This is the inbound half, and it exists because a test double that
drifts does not fail: it certifies the half we wrote. A builder in
`platform_payloads` is only a contract if something checks that the parser
still makes of it what the test believes.

The test iterates the registry rather than a list written here, so a builder
that is added without being registered is caught by
`test_every_platform_registers_its_builders` rather than living unchecked.

Two directions are covered, and both matter:

* A **builder** that drifts from the platform — a field renamed, a nesting
  level dropped — stops parsing, and the case that named what it was for fails.
* A **parser** that stops reading a shape the platform really sends fails the
  same case. That is the direction no render-only test can see.
"""

from __future__ import annotations

from typing import Any

import pytest

from app.modules.agent_surfaces.domain.entities import SurfacePlatform
from app.modules.agent_surfaces.infrastructure.adapters.registry import (
    SurfacePlatformAdapterRegistry,
)
from app.modules.agent_surfaces.platforms.teams.parser import (
    TEAMS_APPROVAL_DECISION_KEY,
    TEAMS_FORM_CALLBACK_KEY,
)
from app.modules.agent_surfaces.tests.e2e import platform_payloads
from app.modules.agent_surfaces.tests.e2e.platform_payloads import (
    teams as teams_payloads,
)
from app.modules.agent_surfaces.tests.e2e.platform_payloads.registry import (
    INBOUND_BUILDERS,
    INTERACTION_BUILDERS,
    InboundCase,
    InteractionCase,
)

pytestmark = pytest.mark.e2e

# Importing the package is what registers the builders; naming it here keeps
# the import from reading as unused.
assert platform_payloads.INBOUND_BUILDERS is INBOUND_BUILDERS


def _inbound_cases() -> list[InboundCase]:
    return [case for build in INBOUND_BUILDERS.values() for case in build()]


def _interaction_cases() -> list[InteractionCase]:
    return [case for build in INTERACTION_BUILDERS.values() for case in build()]


def _assert_fields(parsed: Any, expected: dict[str, Any], label: str) -> None:
    for field, wanted in expected.items():
        actual = getattr(parsed, field)
        if isinstance(wanted, dict) and isinstance(actual, dict):
            # A subset, so a parser adding a metadata key does not fail every
            # case that happens to read one of the others.
            missing = {
                key: (actual.get(key), value)
                for key, value in wanted.items()
                if actual.get(key) != value
            }
            assert not missing, f"{label}: {field}mismatched {missing}"
        else:
            assert actual == wanted, (
                f"{label}: {field} is {actual!r}, the case says {wanted!r}"
            )


def test_every_platform_registers_its_builders() -> None:
    registered = set(INBOUND_BUILDERS)
    assert registered == {
        SurfacePlatform.SLACK.value,
        SurfacePlatform.TEAMS.value,
        SurfacePlatform.TELEGRAM.value,
        SurfacePlatform.WHATSAPP.value,
        SurfacePlatform.RESEND.value,
    }, "a platform with no registered builders is a platform nothing pins"


@pytest.mark.parametrize(
    "case", _inbound_cases(), ids=lambda case: f"{case.platform.value}-{case.name}"
)
async def test_the_parser_reads_each_inbound_payload_as_the_case_says(
    case: InboundCase,
) -> None:
    adapter = SurfacePlatformAdapterRegistry().get(case.platform)
    assert adapter is not None
    parsed = await adapter.parse_inbound_event(case.payload, {})
    if case.refused:
        assert parsed is None, (
            f"{case.platform.value}/{case.name}: the parser accepted a payload "
            "the case says it must refuse"
        )
        return
    assert parsed is not None, (
        f"{case.platform.value}/{case.name}: the parser refused the payload — "
        "either the builder has drifted from the platform, or the parser has "
        "stopped reading a shape the platform really sends"
    )
    assert parsed.platform == case.platform
    _assert_fields(parsed, case.expected, f"{case.platform.value}/{case.name}")


@pytest.mark.parametrize(
    "case", _interaction_cases(), ids=lambda case: f"{case.platform.value}-{case.name}"
)
async def test_the_parser_reads_each_interaction_as_the_case_says(
    case: InteractionCase,
) -> None:
    adapter = SurfacePlatformAdapterRegistry().get(case.platform)
    assert adapter is not None
    parsed = await adapter.parse_inbound_interaction(case.payload, {})
    if case.refused:
        assert parsed is None, (
            f"{case.platform.value}/{case.name}: an interaction the case says "
            "belongs to the message path was claimed by the interaction path"
        )
        return
    assert parsed is not None, f"{case.platform.value}/{case.name}: refused"
    _assert_fields(parsed, case.expected, f"{case.platform.value}/{case.name}")


def test_the_teams_card_keys_are_the_parser_s_own() -> None:
    """The one contract a payload module cannot express as a shape.

    Teams' submitted-card keys are strings the renderer writes and the parser
    reads; a builder spelling its own copy is how the two drift. These are the
    parser's, imported.
    """
    assert teams_payloads.FORM_CALLBACK_KEY == TEAMS_FORM_CALLBACK_KEY
    assert teams_payloads.APPROVAL_DECISION_KEY == TEAMS_APPROVAL_DECISION_KEY
