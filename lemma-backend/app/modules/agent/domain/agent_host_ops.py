"""The Redis half of an ``op``: how a caller on any replica reaches a host.

See docs/architecture/desktop-host-execution.md §3. Only one replica holds a
host's socket, and the caller may be on another. So the caller publishes an
``OpNotice`` on the host's notice channel, naming a one-off reply channel it is
already subscribed to, and the link session holding the socket answers there:
first ``picked_up``, the moment it has claimed the request, then ``result``
once the host has answered the ``op`` frame.

The two messages are separate on purpose. "Nobody took it" and "somebody took
it and the host is slow" are different sentences for an agent: the first means
the Mac is not connected and nothing ran, the second means something may have.
"""

from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.modules.agent.domain.agent_host_link import OP_MAX_DEADLINE_MS
from app.modules.agent.domain.value_objects import JsonObject


#: ``kind`` of an ``AgentHostOpError`` raised on this side rather than by the
#: host: no link took the request within the pickup window.
HOST_OFFLINE = "host_offline"
#: The link that took the request closed before the host answered it.
LINK_LOST = "link_lost"
#: Redis could not carry the request at all.
LINK_UNAVAILABLE = "link_unavailable"
#: The host answered with something that is not an ``op`` answer.
INVALID_ANSWER = "invalid_answer"
#: The deadline passed with the request taken and unanswered.
TIMEOUT = "timeout"

#: What an agent is told when the user's Mac is not there to answer. Written
#: as the sentence the contract promises, because it reaches the model as-is.
HOST_OFFLINE_MESSAGE = (
    "This Mac is not connected, so the command did not run. Lemma Desktop's "
    "Agent Host has to be running and signed in for commands to run on this "
    "computer. Nothing was started; try again once it is connected."
)


class OpNotice(BaseModel):
    """What a caller publishes on the host's notice channel."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["op"] = "op"
    op_id: str = Field(min_length=1, max_length=64)
    reply: str = Field(min_length=1, max_length=256)
    workspace: UUID
    method: str = Field(min_length=1, max_length=64)
    params: JsonObject = Field(default_factory=dict)
    deadline_ms: int = Field(ge=1, le=OP_MAX_DEADLINE_MS)


class OpFailure(BaseModel):
    code: str
    message: str = ""
    retryable: bool = False
    kind: str


class OpReply(BaseModel):
    """What the link session publishes on the reply channel."""

    model_config = ConfigDict(extra="ignore")

    type: Literal["picked_up", "result"]
    result: JsonObject | None = None
    error: OpFailure | None = None


class AgentHostOpError(Exception):
    """An ``op`` did not produce a result.

    ``kind`` is the host's ``detail.kind`` when the host refused, or one of the
    constants above when the request never got that far. ``retryable`` is the
    host's own opinion where it gave one.
    """

    def __init__(self, kind: str, message: str, *, retryable: bool = False) -> None:
        super().__init__(message)
        self.kind = kind
        self.message = message
        self.retryable = retryable
