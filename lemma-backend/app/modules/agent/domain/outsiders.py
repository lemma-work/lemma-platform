"""A conversation that answers somebody outside the pod.

A pod's bot can sit in a group chat alongside people who are not in the pod --
a vendor in a WhatsApp group, a client in a Telegram group. When one of them
asks it something, the run that answers is *the pod's*, acting toward a
stranger, and three things about it differ from every other run:

* **Authority is anonymous.** There is nobody to delegate from, so the run may
  read what the pod has marked Public and nothing else (see
  ``app.core.authorization.anonymous``).
* **The toolset is cut down** to what that authority can use safely -- see
  ``toolset_selection``. A tool that acts as the conversation's owner rather than
  through the authorizer would otherwise hand the stranger the owner's reach.
* **The brief says so**, and says who looks after the conversation, instead of
  describing the owner as the person the agent is talking to.

The fact lives on the conversation, not the run: such a conversation holds only
outsiders' turns, and every consumer that decides what a run may do -- the
runner, the MCP bridge, the approval executor -- already reads the conversation.
"""

from __future__ import annotations

from app.modules.agent.domain.entities import Conversation

#: The metadata key, and the one value of it that means "outsiders".
AUDIENCE_KEY = "audience"
OUTSIDERS = "outsiders"


def answers_outsiders(conversation: Conversation | None) -> bool:
    """Whether this conversation's turns come from people outside the pod."""
    if conversation is None:
        return False
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    return metadata.get(AUDIENCE_KEY) == OUTSIDERS
