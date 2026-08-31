"""What belongs in a bundle, and what is the receiving pod's own business.

A bundle is a pod's *design*, carried somewhere else and rebuilt. Anything the
receiving pod mints for itself is therefore not a bundle's to carry: it would
either collide with what is already there or silently overwrite it.

The pod's own assistant is the case that matters. Every pod has exactly one,
created with the pod, and what it *does* comes from constants rather than from
its row -- so there is nothing in it worth copying and a real hazard in trying.
Stated here rather than in the exporter because the plan builder has to agree:
if a bundle can never contain one, an import must never plan an update against
the target pod's.
"""

from __future__ import annotations

from app.modules.agent.contracts import AgentKind


def is_exportable_agent(agent) -> bool:
    """Whether this agent is one somebody made, rather than one the pod came with."""
    return agent.kind is not AgentKind.POD_DEFAULT
