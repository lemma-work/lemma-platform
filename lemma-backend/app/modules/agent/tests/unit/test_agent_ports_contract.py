"""A port must declare every method its implementation exposes.

`ConversationRepository` is assembled from a class plus several query mixins,
and its Protocol in `domain/ports.py` had drifted to roughly half of that --
nineteen missing declarations by the time #518 counted them. The cost is not
theoretical: #516 shipped two calls that went nowhere, and #445 had to delete a
method from this same Protocol for the mirror-image reason.

basedpyright already catches half of this. It compares the concrete repository
against the port at one line in `api/dependencies.py`, which proves the
implementation satisfies the port -- so a port that declares something nobody
implements fails the build. It says nothing about the other direction: a method
added to the implementation and never declared leaves the gate green. Six were
added that way in the sixty-seven commits after #518 was written, and the one
that happened to be called through a port-typed reference was the only one
anything noticed.

So this test guards the direction the typechecker cannot. It compares names
only; signatures are basedpyright's job at the line above.

The rule: a public method on the repository is part of the contract and belongs
on the port. Anything genuinely internal to the implementation is underscored,
and a port-typed caller cannot reach it.
"""

from __future__ import annotations

import pytest

from app.modules.agent.domain import ports
from app.modules.agent.infrastructure.repositories.agent_repository import (
    AgentRepository,
)
from app.modules.agent.infrastructure.repositories.conversation_repository import (
    ConversationRepository,
)

pytestmark = pytest.mark.unit

# Each pair is checked in the port-declares-everything direction only.
PORTS_AND_IMPLEMENTATIONS = (
    ("ConversationRepository", ports.ConversationRepository, ConversationRepository),
    ("AgentRepository", ports.AgentRepository, AgentRepository),
)


def _public_methods(cls: type) -> set[str]:
    """Public callables contributed by ``cls`` itself, not by its bases."""
    return {
        name
        for name, value in vars(cls).items()
        if not name.startswith("_") and callable(value)
    }


def _declared_on_port(port: type) -> set[str]:
    """What the Protocol declares.

    Read off the class body rather than `typing`'s `__protocol_attrs__`, which
    is an implementation detail of the runtime. These ports inherit from
    `Protocol` directly and from no other port, so the two agree.
    """
    return _public_methods(port)


def _public_methods_of_implementation(impl: type) -> set[str]:
    """Everything a caller can reach, mixins included."""
    return {name for cls in impl.__mro__ for name in _public_methods(cls)}


@pytest.mark.parametrize(
    "name, port, implementation",
    PORTS_AND_IMPLEMENTATIONS,
    ids=[pair[0] for pair in PORTS_AND_IMPLEMENTATIONS],
)
def test_the_port_declares_every_method_its_implementation_exposes(
    name: str, port: type, implementation: type
) -> None:
    undeclared = sorted(
        _public_methods_of_implementation(implementation) - _declared_on_port(port)
    )

    assert not undeclared, (
        f"{implementation.__module__}.{implementation.__name__} exposes {undeclared}, "
        f"which the {name} port does not declare. basedpyright cannot see this: it "
        "only checks that the implementation satisfies the port, so an undeclared "
        "method leaves the build green until someone types a collaborator against "
        "the port, finds the method missing, and annotates that collaborator "
        "`object` instead -- which is how this port lost nineteen declarations. "
        "Declare it on the port, or make it private to the implementation."
    )
