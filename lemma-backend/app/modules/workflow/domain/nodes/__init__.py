"""Workflow node configurations (pure data — executors carry the behavior)."""

from typing import Annotated, Union

from pydantic import Field, TypeAdapter

from app.modules.workflow.domain.nodes.base import (
    BaseNode,
    NodeType,
    RESERVED_NODE_IDS,
)
from app.modules.workflow.domain.nodes.bindings import (
    ExpressionInputBinding,
    InputBinding,
    LiteralInputBinding,
)
from app.modules.workflow.domain.nodes.form import FormNode, FormNodeConfig
from app.modules.workflow.domain.nodes.agent import AgentNode, AgentNodeConfig
from app.modules.workflow.domain.nodes.function import (
    FunctionNode,
    FunctionNodeConfig,
)
from app.modules.workflow.domain.nodes.decision import (
    DecisionNode,
    DecisionNodeConfig,
    DecisionRule,
)
from app.modules.workflow.domain.nodes.loop import LoopNode, LoopNodeConfig
from app.modules.workflow.domain.nodes.wait_until import (
    WaitUntilNode,
    WaitUntilNodeConfig,
)
from app.modules.workflow.domain.nodes.notify import NotifyNode, NotifyNodeConfig
from app.modules.workflow.domain.nodes.end import EndNode, EndNodeConfig

WorkflowNode = Annotated[
    Union[
        FormNode,
        AgentNode,
        FunctionNode,
        DecisionNode,
        LoopNode,
        WaitUntilNode,
        NotifyNode,
        EndNode,
    ],
    Field(discriminator="type"),
]

WORKFLOW_NODE_ADAPTER = TypeAdapter(WorkflowNode)

__all__ = [
    "AgentNode",
    "AgentNodeConfig",
    "BaseNode",
    "DecisionNode",
    "DecisionNodeConfig",
    "DecisionRule",
    "EndNode",
    "EndNodeConfig",
    "ExpressionInputBinding",
    "FormNode",
    "FormNodeConfig",
    "FunctionNode",
    "FunctionNodeConfig",
    "InputBinding",
    "LiteralInputBinding",
    "LoopNode",
    "LoopNodeConfig",
    "NotifyNode",
    "NotifyNodeConfig",
    "NodeType",
    "RESERVED_NODE_IDS",
    "WaitUntilNode",
    "WaitUntilNodeConfig",
    "WorkflowNode",
    "WORKFLOW_NODE_ADAPTER",
]
