"""Notify node configuration (tell a person something)."""

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from app.modules.workflow.domain.nodes.base import BaseNode, NodeType


class NotifyNodeConfig(BaseModel):
    """Who to tell, and what.

    Distinct from a FORM node: a form *blocks* the run until somebody answers,
    which is right when the run needs their input and wrong when it merely needs
    them informed. This node never suspends.
    """

    recipient_user_id: UUID | None = Field(
        default=None, description="Pod member to notify."
    )
    recipient_user_id_expression: str | None = Field(
        default=None,
        description=(
            "Optional JMESPath expression resolving to a pod member id. "
            "Takes precedence over recipient_user_id."
        ),
    )
    message: str = Field(
        description=(
            "What to say. Supports the same expression interpolation as other "
            "node inputs, so it can carry values from earlier steps."
        )
    )
    title: str | None = Field(
        default=None, description="Optional short subject line for the inbox."
    )


class NotifyNode(BaseNode):
    """Notify node. Delivers to the member's freshest channel and always to
    their Lemma inbox, then advances — it does not wait for a reply."""

    type: Literal[NodeType.NOTIFY] = NodeType.NOTIFY
    config: NotifyNodeConfig
