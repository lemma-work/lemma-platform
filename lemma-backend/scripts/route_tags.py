"""Which module owns a route, keyed by its OpenAPI tag.

One dict, because two of them is a bug waiting to be written down twice.
`generate_route_inventory.py` renders `docs/modules/route-inventory.md` from it
and `check_contracts.py` renders `docs/contracts/*.md`; the second used to carry
a copy under a comment saying it was "kept in step with it deliberately: two
documents disagreeing about which module owns a route is its own bug". They were
byte-identical at the time this was extracted, which is the good outcome of that
arrangement and not one to keep relying on.

Adding a tag means adding it here. A tag with no entry is reported by
`generate_route_inventory.py`, so a new one cannot pass through unnoticed.
"""

from __future__ import annotations

TAG_MODULES = {
    "Agent Surfaces": "agent_surfaces",
    "Agent Surfaces (Ingress)": "agent_surfaces",
    "Agent Surfaces (Me)": "agent_surfaces",
    "Apps": "apps",
    "Auth": "identity",
    "Connectors": "connectors",
    "Functions": "function",
    "Organizations": "identity",
    "Pod Bundle": "pod_bundle",
    "Pod Join Requests": "pod",
    "Pod Members": "pod",
    "Pod Permissions": "pod",
    "Pod Resource Access": "pod",
    "Pod Resource Preview": "pod",
    "Pod Roles": "pod",
    "Pods": "pod",
    "Schedules": "schedule",
    "Usage": "usage",
    "Users": "identity",
    "Widgets": "agent",
    "Workspace": "workspace",
    "Workspace Apps": "workspace",
    "agent-tools": "agent",
    "agent_conversations": "agent",
    "agent_host": "agent",
    "agent_runtime": "agent",
    "agents": "agent",
    "files": "datastore",
    "icons": "icon",
    "notifications": "agent_surfaces",
    "query": "datastore",
    "records": "datastore",
    "tables": "datastore",
    "workflows": "workflow",
}
