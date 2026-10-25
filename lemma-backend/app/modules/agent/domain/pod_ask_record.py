"""What a conversation opened by another pod's ask records about it.

The record, not the behaviour: ``pod_asks`` is where asking and answering
are decided. Kept apart because the conversation entity carries an ask as a
typed field, and ``pod_asks`` reads conversations.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from uuid import UUID, uuid4

from app.core.domain.errors import DomainError
from app.modules.agent.domain.value_objects import JsonObject

#: Hops after the first pod. A→B is one, A→B→C two, A→B→C→D three.
MAX_ASK_DEPTH = 3


class AskMode(str, Enum):
    """Whose authority the pod being asked works with."""

    #: As the person the ask is for, who is a member of both pods.
    AS_PERSON = "AS_PERSON"
    #: As the asking pod, over a link the asked pod's people made: Public reads
    #: there plus what they granted it (``core/authorization/pod_principal``).
    LINK = "LINK"


class AskRefused(DomainError):
    """An ask that may not be made, with the reason in words the agent can relay."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="pod_ask_refused", status_code=409)


@dataclass(frozen=True, slots=True)
class AskChain:
    """The pods an ask has passed through, in order, starting with the first asker."""

    chain_id: UUID
    depth: int
    pod_ids: tuple[UUID, ...]

    @classmethod
    def start(cls, from_pod_id: UUID) -> AskChain:
        return cls(chain_id=uuid4(), depth=0, pod_ids=(from_pod_id,))

    def onward_to(self, pod_id: UUID) -> AskChain:
        """This chain, one hop further, or a refusal saying why it can't go."""
        if pod_id in self.pod_ids:
            raise AskRefused(
                "That pod is already part of this request, further up the "
                "chain. Answer with what you have, and say what you still need."
            )
        if self.depth >= MAX_ASK_DEPTH:
            raise AskRefused(
                f"This request has already been passed on {self.depth} times, "
                "which is as far as a request may go. Answer with what you have."
            )
        return AskChain(
            chain_id=self.chain_id,
            depth=self.depth + 1,
            pod_ids=(*self.pod_ids, pod_id),
        )

    def to_json(self) -> JsonObject:
        return {
            "chain_id": str(self.chain_id),
            "depth": self.depth,
            "pod_ids": [str(pod_id) for pod_id in self.pod_ids],
        }

    @classmethod
    def from_json(cls, raw: object) -> AskChain | None:
        if not isinstance(raw, Mapping):
            return None
        try:
            pod_ids = tuple(UUID(str(value)) for value in raw.get("pod_ids") or ())
            return cls(
                chain_id=UUID(str(raw["chain_id"])),
                depth=int(raw["depth"]),
                pod_ids=pod_ids,
            )
        except KeyError, TypeError, ValueError:
            return None


@dataclass(frozen=True, slots=True)
class PodAsk:
    """What B's conversation records about the ask that opened it."""

    from_pod_id: UUID
    from_pod_name: str
    from_conversation_id: UUID
    #: The person behind the ask. Always set when asking as them; over a link,
    #: set when a person started the asking turn and None when nobody did.
    for_user_id: UUID | None
    mode: AskMode
    chain: AskChain
    #: The run in B whose answer has been delivered, so the same answer is
    #: never delivered twice -- once inline to a quick asker and again by the
    #: completion event, say.
    delivered_run_id: UUID | None = None
    #: Until when the asker is waiting for the answer inside its own tool call.
    #: The completion event leaves the answer to it until then, so a quick one
    #: comes back as the tool's result rather than as a message pushed into the
    #: turn that is still waiting for it.
    inline_until: datetime | None = None

    def awaited_inline(self, now: datetime) -> bool:
        return self.inline_until is not None and now < self.inline_until

    def to_json(self) -> JsonObject:
        return {
            "from_pod_id": str(self.from_pod_id),
            "from_pod_name": self.from_pod_name,
            "from_conversation_id": str(self.from_conversation_id),
            "for_user_id": str(self.for_user_id) if self.for_user_id else None,
            "mode": self.mode.value,
            "chain": self.chain.to_json(),
            "delivered_run_id": (
                str(self.delivered_run_id) if self.delivered_run_id else None
            ),
            "inline_until": self.inline_until.isoformat()
            if self.inline_until
            else None,
        }

    @classmethod
    def from_json(cls, raw: object) -> PodAsk | None:
        """The ask, or ``None`` for anything that does not read as one.

        Tolerant on purpose: this is read on every completed run in every pod,
        and a malformed value must cost that conversation its ask, never the
        event handler its run.
        """
        if not isinstance(raw, Mapping):
            return None
        chain = AskChain.from_json(raw.get("chain"))
        if chain is None:
            return None
        try:
            delivered = raw.get("delivered_run_id")
            person = raw.get("for_user_id")
            inline = raw.get("inline_until")
            ask = cls(
                from_pod_id=UUID(str(raw["from_pod_id"])),
                from_pod_name=str(raw.get("from_pod_name") or "Another pod"),
                from_conversation_id=UUID(str(raw["from_conversation_id"])),
                for_user_id=UUID(str(person)) if person else None,
                mode=AskMode(str(raw["mode"])),
                chain=chain,
                delivered_run_id=UUID(str(delivered)) if delivered else None,
                inline_until=datetime.fromisoformat(str(inline)) if inline else None,
            )
        except KeyError, TypeError, ValueError:
            return None
        # Asking as a person with no person is not an ask anybody could make.
        if ask.mode is AskMode.AS_PERSON and ask.for_user_id is None:
            return None
        return ask
