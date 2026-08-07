"""Pod/org resource classes, imported lazily.

Each resource module imports its own slice of the generated API + models. The
client only touches a couple of resources per command, so importing them all
eagerly here would load the whole generated tree on every SDK use. PEP 562
__getattr__ loads each resource module only when its class is first referenced.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

_NAME_TO_MODULE = {
    "AgentHosts": "agent_hosts",
    "PodAgents": "agents",
    "PodConversations": "conversations",
    "PodQueries": "data",
    "PodRecords": "data",
    "PodTables": "data",
    "Table": "data",
    "PodApps": "apps",
    "PodFiles": "files",
    "PodFunctions": "functions",
    "BoundConnectors": "connectors",
    "PodMembers": "members",
    "PodNotifications": "notifications",
    "BoundOrg": "orgs",
    "Orgs": "orgs",
    "BoundOrgRuntime": "runtime",
    "BoundPods": "pods",
    "PodSchedules": "schedules",
    "PodSurfaces": "surfaces",
    "UserSurfaces": "surfaces",
    "Tools": "tools",
    "User": "users",
    "PodWorkflows": "workflows",
}

if TYPE_CHECKING:
    from .agent_hosts import AgentHosts
    from .agents import PodAgents
    from .conversations import PodConversations
    from .data import PodQueries, PodRecords, PodTables, Table
    from .apps import PodApps
    from .files import PodFiles
    from .functions import PodFunctions
    from .connectors import BoundConnectors
    from .members import PodMembers
    from .notifications import PodNotifications
    from .orgs import BoundOrg, Orgs
    from .pods import BoundPods
    from .runtime import BoundOrgRuntime
    from .schedules import PodSchedules
    from .surfaces import PodSurfaces, UserSurfaces
    from .tools import Tools
    from .users import User
    from .workflows import PodWorkflows


def __getattr__(name: str):
    module = _NAME_TO_MODULE.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f".{module}", __name__), name)
    globals()[name] = value  # cache for subsequent lookups
    return value


def __dir__() -> list[str]:
    return sorted(_NAME_TO_MODULE)


__all__ = [
    "AgentHosts",
    "BoundConnectors",
    "BoundOrg",
    "BoundPods",
    "Orgs",
    "BoundOrgRuntime",
    "PodAgents",
    "PodConversations",
    "PodApps",
    "PodFiles",
    "PodFunctions",
    "PodMembers",
    "PodNotifications",
    "PodQueries",
    "PodRecords",
    "PodSchedules",
    "PodSurfaces",
    "UserSurfaces",
    "PodTables",
    "PodWorkflows",
    "Table",
    "Tools",
    "User",
]
