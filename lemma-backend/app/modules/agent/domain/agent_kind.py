"""Whether somebody made this agent, or the pod came with it.

A module of its own because ``value_objects`` was at the 600-line ceiling,
and this is the one value object every other module has to name: the
schedule module, the bundle exporter and the surfaces module all ask which
kind an agent is, and none of them should have to import the whole vocabulary
of message roles and run statuses to do it.
"""

from __future__ import annotations

from enum import Enum


class AgentKind(str, Enum):
    """Whether somebody made this agent, or the pod came with it.

    The pod's default assistant used to be the absence of an agent: a
    conversation naming nobody, synthesised at runtime against one sentinel id
    shared by every pod. That absence could not be pointed at by a foreign key,
    so anything wanting to name it grew its own way of saying so — a boolean on
    the schedule, a second boolean on a channel route, a magic string in a map
    of who answers whose DMs.

    A kind is one way of saying it, in the row itself. ``POD_DEFAULT`` is
    pinned by check constraints to exactly one row per pod, whose id is the
    pod's own — so "is this the default assistant?" stays a comparison rather
    than a query, which matters on paths that answer it per request.
    """

    USER = "USER"
    POD_DEFAULT = "POD_DEFAULT"
