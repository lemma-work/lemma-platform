"""A conversation that answers somebody outside the pod.

Everybody outside the pod the bot talks to is a **contact**: a customer on
WhatsApp, a client in a Telegram group, a vendor by email, a visitor on a web
page nobody has vouched for yet. The run that answers one is *the pod's*,
acting toward them, and three things about it differ from every other run:

* **Authority is the contact's.** There is nobody to delegate from, so the run
  may read what the pod has marked Public and the rows the contact owns, and
  nothing else (see ``app.core.authorization.anonymous``).
* **The toolset is cut down** to what that authority can use safely -- see
  ``toolset_selection``. A tool that acts as the conversation's owner rather than
  through the authorizer would otherwise hand the contact the owner's reach.
* **The brief says so**, and says who looks after the conversation, instead of
  describing the owner as the person the agent is talking to.

Whom a conversation answers is a column, ``contact_id``, written by the server
when it opens the conversation and by nothing a client sends. Every consumer
that decides what a run may do -- the runner, the MCP bridge, the approval
executor -- already reads the conversation, so it reads one answer.

**Another pod asking over a link is somebody outside the pod too.** Its
conversation is the one the ask opened (``pod_asks``), and its ``Audience`` is
``pod``: every rule above holds, and the one difference is whose authority the
tools check -- the asking pod's grants here (``authorization.pod_principal``)
instead of a contact's.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum
from typing import Self
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

from app.core.domain.errors import DomainError
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.pod_asks import linked_asker_in
from app.modules.agent.domain.value_objects import AgentToolset

#: A conversation with people outside the pod that a member answers by hand
#: for now, so the agent stays quiet in it: ``{"user_id", "until"}``. Not a
#: protected key -- a member who drops it from the conversation's metadata has
#: handed the conversation back.
HANDED_TO_KEY = "handed_to"


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


class AudienceKind(StrEnum):
    """Whom a conversation answers."""

    MEMBER = "member"
    #: One contact, named by the conversation's ``contact_id``.
    CONTACT = "contact"
    #: Another pod, asking over a link: read off the conversation's ``ask``,
    #: which only the server writes.
    POD = "pod"


class Audience(BaseModel):
    """Whom one run answers, decided once and carried on everything the run builds.

    Read off the conversation by :meth:`of` and nowhere else, so the
    authorizer, the toolset, the brief, the private-note labels and the
    metering scope cannot each reach a different answer from the same row.

    ``contact_id`` is set exactly when ``kind`` is ``CONTACT``, and
    ``asking_pod_id`` exactly when it is ``POD``.
    """

    model_config = ConfigDict(frozen=True)

    kind: AudienceKind = AudienceKind.MEMBER
    contact_id: UUID | None = None
    asking_pod_id: UUID | None = None

    @model_validator(mode="after")
    def _contact_id_only_for_a_contact(self) -> Self:
        if (self.kind is AudienceKind.CONTACT) != (self.contact_id is not None):
            raise ValueError("contact_id is set exactly for a contact audience")
        if (self.kind is AudienceKind.POD) != (self.asking_pod_id is not None):
            raise ValueError("asking_pod_id is set exactly for a pod audience")
        return self

    @classmethod
    def member(cls) -> Audience:
        return cls()

    @classmethod
    def contact(cls, contact_id: UUID) -> Audience:
        return cls(kind=AudienceKind.CONTACT, contact_id=contact_id)

    @classmethod
    def pod(cls, asking_pod_id: UUID) -> Audience:
        return cls(kind=AudienceKind.POD, asking_pod_id=asking_pod_id)

    @classmethod
    def of(cls, conversation: Conversation | None) -> Audience:
        """The audience ``conversation`` answers; a member's when there is none.

        Read with ``getattr``: the readers that decide what a run may do are
        handed things shaped like a conversation -- a surface's view of one, a
        stored row -- and a shape without the column answers a member's.
        """
        contact_id = getattr(conversation, "contact_id", None)
        if isinstance(contact_id, UUID):
            return cls.contact(contact_id)
        asking_pod = linked_asker_in(getattr(conversation, "metadata", None))
        if asking_pod is not None:
            return cls.pod(asking_pod)
        return cls.member()

    @property
    def answers_outsiders(self) -> bool:
        """True for a contact and a linked pod alike: neither is a member, and
        every rule about what such a run may do holds for both. Only the
        authority its tools check differs (``tools/authority``)."""
        return self.kind is not AudienceKind.MEMBER

    @property
    def is_contact(self) -> bool:
        return self.kind is AudienceKind.CONTACT

    @property
    def is_linked_pod(self) -> bool:
        return self.kind is AudienceKind.POD


def run_audience(deps: object) -> Audience:
    """The audience a run's context carries, or a member's when it carries none.

    For readers handed something shaped like an ``AgentContext`` that may not
    be one -- a bare ``RunContext.deps`` in a capability, say.
    """
    audience = getattr(deps, "audience", None)
    return audience if isinstance(audience, Audience) else Audience.member()


def handed_to(metadata: dict[str, object] | None, *, now: datetime) -> UUID | None:
    """The member answering this conversation by hand, while that lasts.

    A hand-off ends by itself at ``until``: one made because the contacts cap
    was reached lasts the month the cap counts, and the bot answering again
    when the budget renews is the hand-back nobody has to remember.
    """
    raw = (metadata or {}).get(HANDED_TO_KEY)
    if not isinstance(raw, dict):
        return None
    try:
        member = UUID(str(raw.get("user_id")))
        until = datetime.fromisoformat(str(raw.get("until")))
    except ValueError:
        return None
    if until.tzinfo is None:
        return None
    return member if until > now else None


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
    if run_audience(deps).answers_outsiders:
        raise OutsiderRunRefused(
            "A workspace is not available when answering someone outside the pod."
        )
