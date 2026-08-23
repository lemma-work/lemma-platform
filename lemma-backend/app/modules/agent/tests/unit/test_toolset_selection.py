"""The three sources an agent's toolsets come from, and the order they combine in.

These rules used to be one implication and one subtraction. They are now the
difference between a person answering five questions in the agent editor and
answering twelve, so they are worth pinning down away from a database.
"""

from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.core.authorization.context import ResourceType
from app.modules.agent.domain.value_objects import AgentToolset
from app.modules.agent.tools.toolset_selection import (
    ALWAYS_ON_TOOLSETS,
    DECLARABLE_TOOLSETS,
    AgentGrantSummary,
    derived_toolsets,
    resolve_toolsets,
)

pytestmark = pytest.mark.unit


def _agent(*toolsets: AgentToolset):
    return SimpleNamespace(
        id=uuid4(), pod_id=uuid4(), user_id=uuid4(), name="a", toolsets=list(toolsets)
    )


def _conversation(*, is_sub_agent: bool = False):
    metadata = {"is_sub_agent": True} if is_sub_agent else {}
    return SimpleNamespace(id=uuid4(), metadata=metadata)


def _grants(*resource_types: ResourceType) -> AgentGrantSummary:
    return AgentGrantSummary.from_grants(
        [(resource_type.value, uuid4()) for resource_type in resource_types]
    )


def test_the_declarable_and_always_on_sets_never_overlap():
    """A toolset is either a person's decision or it is not. Both would mean the
    editor shows a switch that changes nothing."""
    assert not set(DECLARABLE_TOOLSETS) & set(ALWAYS_ON_TOOLSETS)


def test_the_derived_ones_are_in_neither_set():
    """POD and CONNECTORS come from a grant. Listing them as declarable would put
    the same permission behind two controls, which is what this replaced."""
    for toolset in (AgentToolset.POD, AgentToolset.CONNECTORS):
        assert toolset not in DECLARABLE_TOOLSETS
        assert toolset not in ALWAYS_ON_TOOLSETS


def test_view_image_is_never_declarable():
    """It is appended at run time from the model's own vision capability and is
    never persisted, so offering it as a switch would be offering a no-op."""
    assert AgentToolset.VIEW_IMAGE not in DECLARABLE_TOOLSETS
    assert AgentToolset.VIEW_IMAGE not in ALWAYS_ON_TOOLSETS


def test_every_agent_gets_the_always_on_set():
    resolved = resolve_toolsets(_agent(), _conversation())

    assert set(ALWAYS_ON_TOOLSETS) <= set(resolved.names)


def test_a_declarable_toolset_is_never_implied():
    resolved = resolve_toolsets(_agent(AgentToolset.WEB_SEARCH), _conversation())

    assert AgentToolset.WEB_SEARCH in resolved.names
    for toolset in DECLARABLE_TOOLSETS:
        if toolset is not AgentToolset.WEB_SEARCH:
            assert toolset not in resolved.names


@pytest.mark.parametrize(
    "resource_type",
    [
        ResourceType.FOLDER,
        ResourceType.DOCUMENT,
        ResourceType.DATASTORE_TABLE,
        ResourceType.DATASTORE_RECORD,
    ],
)
def test_any_data_grant_implies_pod_access(resource_type):
    """Granting a folder and then ticking 'pod data' asked the same question
    twice, and forgetting the second half failed silently."""
    resolved = resolve_toolsets(
        _agent(), _conversation(), grants=_grants(resource_type)
    )

    assert AgentToolset.POD in resolved.names
    assert AgentToolset.POD in resolved.derived


@pytest.mark.parametrize(
    "resource_type",
    [
        ResourceType.CONNECTOR,
        ResourceType.CONNECTOR_ACCOUNT,
        ResourceType.CONNECTOR_AUTH_CONFIG,
    ],
)
def test_any_connector_grant_implies_the_connector_tools(resource_type):
    """The toolset never granted anything on its own — reaching an app always
    needed the per-app grant as well."""
    resolved = resolve_toolsets(
        _agent(), _conversation(), grants=_grants(resource_type)
    )

    assert AgentToolset.CONNECTORS in resolved.names


def test_a_function_grant_alone_implies_neither():
    """Being allowed to run a function says nothing about reading pod data or
    reaching a connected app."""
    resolved = resolve_toolsets(
        _agent(), _conversation(), grants=_grants(ResourceType.FUNCTION)
    )

    assert AgentToolset.POD not in resolved.names
    assert AgentToolset.CONNECTORS not in resolved.names


def test_no_grants_derives_nothing():
    assert derived_toolsets(None) == frozenset()
    assert derived_toolsets(AgentGrantSummary()) == frozenset()


def test_a_sub_agent_loses_the_withheld_ones_even_though_they_are_always_on():
    """The subtraction has to run last.

    MESSAGING and SNOOZE are universal now, so an ordering that added them after
    the sub-agent filter would hand a child run the two toolsets it is
    specifically withheld from.
    """
    resolved = resolve_toolsets(_agent(), _conversation(is_sub_agent=True))

    assert AgentToolset.MESSAGING not in resolved.names
    assert AgentToolset.SNOOZE not in resolved.names
    assert AgentToolset.SUBAGENTS not in resolved.names
    # The rest of the always-on set survives.
    assert AgentToolset.USER_INTERACTION in resolved.names
    assert AgentToolset.TODO in resolved.names
    assert resolved.allow_subagents is False


def test_a_stale_declared_toolset_is_harmless():
    """Nothing migrates existing rows, so agents still carry POD and CONNECTORS
    from before they were derived. The effective set is a union, so a stale entry
    simply stops mattering — it must not duplicate or raise."""
    resolved = resolve_toolsets(
        _agent(AgentToolset.POD, AgentToolset.CONNECTORS),
        _conversation(),
        grants=_grants(ResourceType.FOLDER),
    )

    assert resolved.names.count(AgentToolset.POD) == 1
    assert resolved.names.count(AgentToolset.CONNECTORS) == 1


def test_an_unknown_name_on_an_old_row_is_skipped():
    resolved = resolve_toolsets(_agent("RETIRED_TOOLSET"), _conversation())

    assert set(ALWAYS_ON_TOOLSETS) <= set(resolved.names)


def test_the_pod_default_assistant_still_gets_its_fixed_set():
    """A run with no agent is Lem, which has no grants of its own — it runs with
    the user's permissions and takes its toolsets from the default set."""
    from app.modules.agent.tools.registry import POD_DEFAULT_AGENT_TOOLSETS

    resolved = resolve_toolsets(None, _conversation())

    assert set(POD_DEFAULT_AGENT_TOOLSETS) <= set(resolved.names)
