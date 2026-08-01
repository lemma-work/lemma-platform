"""Base node config. Nodes are pure configuration — behavior lives in
execution/executors, resolved by node type via the executor registry."""

from enum import Enum
from typing import Dict

from pydantic import BaseModel


class NodeType(str, Enum):
    """Type of workflow node."""

    FORM = "FORM"
    AGENT = "AGENT"
    FUNCTION = "FUNCTION"
    DECISION = "DECISION"
    LOOP = "LOOP"
    WAIT_UNTIL = "WAIT_UNTIL"
    # Tell a person something and carry on. A FORM blocks the run until somebody
    # answers; this one never suspends.
    NOTIFY = "NOTIFY"
    END = "END"


# Context namespaces that node ids must not collide with.
RESERVED_NODE_IDS = frozenset({"start", "loop"})


class BaseNode(BaseModel):
    """Shared node fields. Configuration only — no behavior."""

    id: str
    label: str | None = None
    position: Dict[str, float] | None = None
