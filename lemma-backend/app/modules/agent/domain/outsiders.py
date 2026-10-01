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

from app.core.domain.errors import DomainError
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import AgentToolset

#: The metadata key, and the one value of it that means "outsiders".
AUDIENCE_KEY = "audience"
OUTSIDERS = "outsiders"

#: The toolsets a stranger's run keeps, when its agent has them. A first cut:
#: inside them, only the tools named in ``tools/outsider_tools`` survive.
#:
#: POD        -- every call goes through the authorizer, which for this run is
#:               anonymous, so what reaches the stranger is what is Public.
#: WEB_SEARCH -- for ``web_search`` alone. Its ``web_fetch`` opens the
#:               conversation owner's sandbox and is withheld by name.
#: MESSAGING  -- fenced to the member who looks after the conversation (see
#:               ``message_user``), so the agent can pass a question on.
#:
#: Everything else acts as the conversation's owner rather than through the
#: authorizer -- a sandbox session, a browser, a connector account, sub-agents
#: running as them -- or pauses the run on a decision nobody in the group can
#: make (USER_INTERACTION, WAIT). MEMORY goes because the owner's memory is
#: theirs; TODO because the conversation's task list can be written by the
#: owner's own private-note runs, and every later stranger's prompt shows it.
OUTSIDER_TOOLSETS = frozenset(
    {
        AgentToolset.POD,
        AgentToolset.WEB_SEARCH,
        AgentToolset.MESSAGING,
    }
)


#: The tool that records a member's answer to a question passed on to them. On a
#: question from outside the pod it runs only as an approved call, and each
#: approval covers exactly one answer -- never "for the rest of the session".
OUTSIDE_ANSWER_TOOL = "respond_to_notification"


def answers_outsiders(conversation: Conversation | None) -> bool:
    """Whether this conversation's turns come from people outside the pod."""
    if conversation is None:
        return False
    metadata = conversation.metadata if isinstance(conversation.metadata, dict) else {}
    return metadata.get(AUDIENCE_KEY) == OUTSIDERS


def with_audience_kept(
    existing: dict[str, object] | None, incoming: dict[str, object] | None
) -> dict[str, object] | None:
    """``incoming`` metadata, with whom the conversation answers kept as stored.

    The audience is written once, by the surface that opened the conversation,
    and it is what makes every run in it authorize as nobody. A client that
    replaces the metadata wholesale -- or anything holding the owner's token,
    the owner's own agent talked into it included -- must not be able to drop
    it, and have the next stranger's turn run with the owner's authority.
    Metadata cleared where there is no audience to keep stays cleared.
    """
    previous = (existing or {}).get(AUDIENCE_KEY)
    if incoming is None and previous is None:
        return None
    kept = dict(incoming or {})
    kept.pop(AUDIENCE_KEY, None)
    if previous is not None:
        kept[AUDIENCE_KEY] = previous
    return kept


def without_audience(metadata: dict[str, object] | None) -> dict[str, object] | None:
    """Client-supplied metadata, minus any claim about whom the conversation answers.

    Only routing opens a conversation for people outside the pod
    (``open_surface_conversation``); a conversation a client creates is its own.
    """
    if not metadata or AUDIENCE_KEY not in metadata:
        return metadata
    return {key: value for key, value in metadata.items() if key != AUDIENCE_KEY}


class OutsiderRunRefused(DomainError):
    """A run answering somebody outside the pod was about to get more than it may.

    Raised where the outsider rules cannot be kept -- no runtime that runs in
    this process, or an Agent Host payload being built for such a run -- so the
    stranger goes unanswered rather than answered with the member's reach.
    """

    def __init__(self, message: str) -> None:
        super().__init__(message, code="outsider_run_refused", status_code=409)


def refuse_owner_workspace(deps: object) -> None:
    """Raise if a run answering somebody outside the pod reaches for a workspace.

    A sandbox session, a host session and the workspace file manager are all
    the conversation owner's: their files, their environment, a Lemma token
    minted for them, a browser signed in as them. No tool a stranger's run is
    allowed reaches one, and this is where any that tries is stopped, whatever
    let it through.
    """
    if getattr(deps, "answers_outsider", False):
        raise OutsiderRunRefused(
            "A workspace is not available when answering someone outside the pod."
        )
