"""What a person does with agents and conversations."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any

from harness.run import a_name_for
from harness.drivers.api import every_item, items_of
from harness.waiting import eventually, UNTIL_A_MODEL_ACTS

JSON = dict[str, Any]

#: A run has finished when it reaches one of these. Anything else means it is
#: still going, and a scenario asserting on the answer has to keep waiting.
#: A run that has stopped of its own accord and will do nothing more.
FINISHED = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED"}

#: A run that has stopped and is waiting for a person to decide something. The
#: turn finished — `last_run_status` is COMPLETED — but the conversation will
#: not move again until somebody answers.
PAUSED_FOR_A_PERSON = {"WAITING", "WAITING_FOR_INPUT"}

#: What "settled" means to a scenario waiting for a run: either of the above.
#: `WAITING` was missing until a real model started asking for approvals of its
#: own accord — the scripted one only ever produced `WAITING_FOR_INPUT`, so the
#: gap sat behind the seam for as long as the seam existed.
#:
#: The distinction matters, and getting it wrong is silent: a helper that answers
#: approvals must treat `WAITING` as work still to do, or it returns the moment
#: the first question is asked and reports that nobody was asked anything.
SETTLED = FINISHED | PAUSED_FOR_A_PERSON


#: The scripted-model seam used to live here: a scenario passed the tool calls
#: it wanted through `conversation.metadata`, and a deterministic model made
#: them. It is gone, and deliberately.
#:
#: It proved that Lemma dispatched, authorized and approved *that call*. It
#: could not prove that a sentence a person actually typed ended up refused,
#: which is the promise. And against a deployment it was worse than useless:
#: `e2e_llm_mode` is `real` there, so the metadata was ignored in silence and
#: the scenario asserted a scripted model's behaviour against a thinking one.
#: One scenario in the live lane had been doing exactly that for months.
#:
#: What replaced it is the product's own lever: an agent's `instruction`. Tell
#: an agent to ask before it changes anything — which is what a person does when
#: they set one up — then say what a person would say, and assert on what must
#: be true afterwards. Scenarios that need this take `needs(MODEL_IS_REAL)` and
#: skip with a reason where there is no model to think with.


def _approval_id(approval: JSON) -> str:
    """How an approval is addressed when deciding it.

    An approval is a tool call awaiting an answer, and the decision endpoint
    keys it by ``tool_call_id`` — not by the id of the message carrying it.
    Sending the message id is accepted and matches nothing, so the run stays
    paused and the scenario times out somewhere else entirely.
    """
    identifier = approval.get("tool_call_id") or approval.get("id")
    if not identifier:
        raise AssertionError(f"this does not look like an approval: {sorted(approval)}")
    return str(identifier)


class AgentSteps:
    """Mixed into :class:`harness.world.Person`."""

    # --- defining agents -------------------------------------------------

    async def creates_an_agent(
        self,
        *,
        in_pod: JSON,
        named: str | None = None,
        instruction: str = "You help with whatever is asked.",
        toolsets: list[str] | None = None,
        visibility: str | None = None,
    ) -> JSON:
        name = named or a_name_for("agent")
        body: JSON = {"name": name, "instruction": instruction}
        if toolsets is not None:
            body["toolsets"] = toolsets
        if visibility is not None:
            body["visibility"] = visibility
        return await self.api.post(
            f"/pods/{in_pod['id']}/agents",
            what=f"{self.label} creating agent {name!r}",
            json=body,
        )

    async def is_refused_creating_an_agent(
        self, *, in_pod: JSON, named: str, instruction: str = "anything"
    ) -> int:
        response = await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/agents",
            json={"name": named, "instruction": instruction},
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused creating agent "
                f"{named!r}, but it succeeded ({response.status_code})"
            )
        return response.status_code

    async def opens_agent(self, name: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/agents/{name}",
            what=f"{self.label} opening agent {name!r}",
        )

    async def has_agent(self, name: str, *, in_pod: JSON) -> bool:
        """Is this agent in this pod? Asked by name, and that is the point.

        `agents_in` is the obvious way to answer it and the wrong one: the list
        endpoint pages at 100, so on a pod holding more than that "not in the
        list" means "not on the first page" and nothing more. Provisioning read
        it as "does not exist", tried to create the standing agent, and a real
        deployment answered 409 for a name that had been there all along.
        """
        answered = await self.api.call("GET", f"/pods/{in_pod['id']}/agents/{name}")
        return answered.status_code == 200

    async def agents_in(self, pod: JSON) -> list[JSON]:
        """Every agent in the pod, following the pages.

        The cap here has already broken provisioning once: the list stops at
        100, `works_in` decided whether the standing agent existed by looking
        for it, and once a standing pod held more than a page of leftovers the
        deployment answered 409 for a name that had been there all along. See
        `has_agent`, which is still the right way to ask about one by name.
        """
        return await every_item(
            lambda params: self.api.get(f"/pods/{pod['id']}/agents", params=params)
        )

    async def deletes_agent(self, name: str, *, in_pod: JSON) -> None:
        await self.api.delete(
            f"/pods/{in_pod['id']}/agents/{name}",
            what=f"{self.label} deleting agent {name!r}",
        )

    async def changes_agent_toolsets(
        self, name: str, *, in_pod: JSON, to: list[str]
    ) -> JSON:
        return await self.api.patch(
            f"/pods/{in_pod['id']}/agents/{name}",
            what=f"{self.label} changing what agent {name!r} can do",
            json={"toolsets": to},
        )

    async def grants_of_agent(self, name: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/agents/{name}/permissions")

    # --- conversations ---------------------------------------------------

    async def starts_a_conversation(
        self,
        *,
        in_pod: JSON,
        with_agent: str | None = None,
        saying: str | None = None,
        under: JSON | None = None,
    ) -> JSON:
        """Open a thread, and optionally say the first thing.

        Creating a conversation and sending a message are two calls: the create
        body has no message field, so passing one is silently ignored and the
        agent is never asked anything. Bundling them here means a scenario says
        "starts a conversation saying X" and gets what it asked for.

        ``under`` makes this thread a
        child of another, which is how a subagent's work stays attached to the
        conversation that delegated it.
        """
        body: JSON = {}
        if with_agent:
            body["agent_name"] = with_agent
        if under is not None:
            body["parent_id"] = str(under["id"])
        conversation = await self.api.post(
            f"/pods/{in_pod['id']}/conversations",
            what=f"{self.label} starting a conversation in {in_pod.get('name')!r}",
            json=body,
        )
        self.conversation = conversation
        if saying:
            await self.says(saying, in_conversation=conversation, in_pod=in_pod)
        return conversation

    async def conversations_in(self, pod: JSON, *, pages: int = 20) -> list[JSON]:
        """Every conversation in the pod, following the pages.

        Asking once returns twenty, ordered by id descending — and ids are
        time-ordered, so that is newest first. On a pod that stands between runs
        this buries anything an earlier run opened under everything opened
        since, and a scenario looking for it concluded the product had lost the
        message. The same shape as the agent list capped at 100: a default page
        size read as "all of them".

        `pages` is a bound rather than a limit anyone should hit — twenty pages
        of a hundred is two thousand conversations. It exists so a bug in the
        cursor cannot spin here forever.
        """
        return await every_item(
            lambda params: self.api.get(
                f"/pods/{pod['id']}/conversations", params=params
            ),
            pages=pages,
        )

    async def opens_conversation(self, conversation: JSON, *, in_pod: JSON) -> JSON:
        return await self.api.get(
            f"/pods/{in_pod['id']}/conversations/{conversation['id']}"
        )

    async def is_refused_conversation(self, conversation: JSON, *, in_pod: JSON) -> int:
        response = await self.api.call(
            "GET", f"/pods/{in_pod['id']}/conversations/{conversation['id']}"
        )
        if response.status_code < 400:
            raise AssertionError(
                f"{self.label} was expected to be refused a conversation they do "
                f"not own, but read it ({response.status_code})"
            )
        return response.status_code

    async def says(self, message: str, *, in_conversation: JSON, in_pod: JSON) -> JSON:
        return await self.api.post(
            f"/pods/{in_pod['id']}/conversations/{in_conversation['id']}/messages",
            what=f"{self.label} sending a message",
            json={"content": message},
        )

    async def adds_while_it_works(
        self, message: str, *, in_conversation: JSON, in_pod: JSON
    ) -> JSON:
        """Say something without opening a stream to watch the answer arrive.

        Not ``adds``: ``PodSteps.adds`` already owns that name, and ``Person``
        mixes both in -- a second one is shadowed in silence and the scenario
        fails on an argument name three files away from the cause.

        What a person does when the agent is already working: the message joins
        the run in flight rather than starting a second one. ``says`` is the
        wrong call for that -- it opens a second Server-Sent Events
        subscription for a run that already has one.
        """
        return await self.api.post(
            f"/pods/{in_pod['id']}/conversations/{in_conversation['id']}/messages/append",
            what=f"{self.label} adding a message to a run already working",
            json={"content": message},
        )

    async def messages_in(self, conversation: JSON, *, in_pod: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/pods/{in_pod['id']}/conversations/{conversation['id']}/messages"
            )
        )

    async def waits_for_a_reply(
        self,
        *,
        in_conversation: JSON,
        in_pod: JSON,
        after: int = 0,
        timeout: float = 60.0,
    ) -> list[JSON]:
        """Wait until the agent has added at least one message beyond ``after``.

        Runs are queued to the worker, so the reply is not in the response to
        sending the message. Polling the transcript is exactly what a client
        without a stream does.
        """
        return await eventually(
            lambda: self.messages_in(in_conversation, in_pod=in_pod),
            lambda messages: (
                len(messages) > after
                and any(m.get("role") == "assistant" for m in messages)
            ),
            describe=(f"the agent to reply in conversation {in_conversation['id']}"),
            timeout=timeout,
        )

    async def waits_for_the_run_to_settle(
        self, *, conversation: JSON, in_pod: JSON, timeout: float = 60.0
    ) -> JSON:
        return await eventually(
            lambda: self.opens_conversation(conversation, in_pod=in_pod),
            lambda payload: (
                str(payload.get("status") or "").upper() in SETTLED
                or payload.get("status") is None
            ),
            describe=f"conversation {conversation['id']} to reach a terminal state",
            timeout=timeout,
        )

    async def changes_agent(self, name: str, *, in_pod: JSON, **changes: Any) -> JSON:
        return await self.api.patch(
            f"/pods/{in_pod['id']}/agents/{name}",
            what=f"{self.label} updating agent {name!r}",
            json=changes,
        )

    async def replaces_agent_grants(
        self, name: str, *, grants: list[JSON], in_pod: JSON
    ) -> JSON:
        return await self.api.put(
            f"/pods/{in_pod['id']}/agents/{name}/permissions",
            what=f"{self.label} replacing what agent {name!r} may reach",
            json={"grants": grants},
        )

    async def renames_conversation(
        self, conversation: JSON, *, to: str, in_pod: JSON
    ) -> JSON:
        return await self.api.patch(
            f"/pods/{in_pod['id']}/conversations/{conversation['id']}",
            what=f"{self.label} retitling a conversation",
            json={"title": to},
        )

    async def retries(self, conversation: JSON, *, in_pod: JSON) -> Any:
        return await self.api.call(
            "POST", f"/pods/{in_pod['id']}/conversations/{conversation['id']}/retry"
        )

    async def approvals_in(self, conversation: JSON, *, in_pod: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/pods/{in_pod['id']}/conversations/{conversation['id']}/approvals"
            )
        )

    async def decides(
        self,
        approval: JSON,
        *,
        allow: bool,
        conversation: JSON,
        in_pod: JSON,
        for_the_session: bool = False,
    ) -> Any:
        """Answer an approval request.

        ``for_the_session`` is the "stop asking me this" answer. It is a
        different decision, not a flag on the same one, and the product scopes
        it to this conversation — which is the whole of PS-ACCESS-022.
        """
        if allow:
            decision = "APPROVE_FOR_SESSION" if for_the_session else "APPROVE_ONCE"
        else:
            decision = "DENY"
        return await self.api.call(
            "POST",
            f"/pods/{in_pod['id']}/conversations/{conversation['id']}"
            f"/approvals/{_approval_id(approval)}/decision",
            json={"decision": decision, "response": {}},
        )

    async def answers_approval(
        self,
        approval: JSON,
        *,
        allow: bool,
        conversation: JSON,
        in_pod: JSON,
        for_the_session: bool = False,
    ) -> JSON:
        """Answer an approval and insist it was accepted.

        :meth:`decides` returns the raw response so a scenario can assert on a
        refusal. This one is for the ordinary path, where a decision that
        quietly failed would leave the run paused and every later assertion
        would be about a run that never resumed rather than about the product.
        """
        response = await self.decides(
            approval,
            allow=allow,
            conversation=conversation,
            in_pod=in_pod,
            for_the_session=for_the_session,
        )
        if response.status_code >= 400:
            raise AssertionError(
                f"{self.label} could not answer the approval "
                f"({response.status_code}): {response.text[:500]}"
            )
        return response.json() if response.content else {}

    async def waits_for_an_approval_in(
        self,
        conversation: JSON,
        *,
        in_pod: JSON,
        after: int = 0,
        timeout: float = UNTIL_A_MODEL_ACTS,
    ) -> list[JSON]:
        """Wait until the run has asked a person for permission.

        On the model budget, not the run one. What is being waited for is a
        model reading its instructions and *choosing* to call the question
        tool — a queued run reaches that two turns in, and at sixty seconds
        this reported "it never happened" three times in thirty-one runs while
        the product was working perfectly.
        """
        return await eventually(
            lambda: self.approvals_in(conversation, in_pod=in_pod),
            lambda requests: len(requests) > after,
            describe=(
                f"an approval request in conversation {conversation['id']} — "
                f"the agent was told to ask before acting and had "
                f"{timeout:.0f}s to do it"
            ),
            timeout=timeout,
        )

    async def answers_every_approval(
        self,
        conversation: JSON,
        *,
        allow: bool,
        in_pod: JSON,
        for_the_session: bool = False,
        timeout: float = 90.0,
    ) -> int:
        """Stay with a run, answering each thing it asks, until it finishes.

        A run can stop more than once: each denied permission is a separate
        question, and a person watching answers them as they arrive. Deciding a
        single approval and then waiting for the run to settle would hang on the
        second question — which looks like the product being broken and is only
        the scenario having walked away.

        Returns how many times it was asked.
        """
        deadline = time.monotonic() + timeout
        # A decision is not instantly reflected in the pending list, so without
        # remembering what has been answered this answers the same request
        # several times and reports a count nobody asked for.
        answered: set[str] = set()
        while time.monotonic() < deadline:
            state = await self.opens_conversation(conversation, in_pod=in_pod)
            # FINISHED, not SETTLED: a run paused on an approval is exactly what
            # this loop is for, and treating it as done returns before answering
            # a single question.
            if str(state.get("status") or "").upper() in FINISHED:
                return len(answered)
            for request in await self.approvals_in(conversation, in_pod=in_pod):
                identifier = _approval_id(request)
                if identifier in answered:
                    continue
                await self.answers_approval(
                    request,
                    allow=allow,
                    conversation=conversation,
                    in_pod=in_pod,
                    for_the_session=for_the_session,
                )
                answered.add(identifier)
            await asyncio.sleep(0.1)
        raise AssertionError(
            f"conversation {conversation['id']} never finished; "
            f"{self.label} answered {len(answered)} approvals in {timeout:.0f}s"
        )

    async def transcript_of(self, conversation: JSON, *, in_pod: JSON) -> str:
        """The whole thread flattened to text, for asserting on what happened.

        Tool calls and their results are carried in message parts whose shape
        differs by role, so a scenario that wants to know "did the agent get
        told no" reads the transcript rather than guessing at the envelope.
        """
        return json.dumps(
            await self.messages_in(conversation, in_pod=in_pod), default=str
        )

    async def watches(self, conversation: JSON, *, in_pod: JSON) -> tuple[int, str]:
        """Open the conversation stream and take what it says first."""
        return await self.api.opens_stream(
            f"/pods/{in_pod['id']}/conversations/{conversation['id']}/stream"
        )

    # --- runtime profiles -------------------------------------------------

    async def runtime_profiles_in(self, organization: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/organizations/{organization['id']}/agent-runtime/profiles"
            )
        )

    async def opens_runtime_profile(
        self, profile_id: str, *, in_organization: JSON
    ) -> JSON:
        return await self.api.get(
            f"/organizations/{in_organization['id']}/agent-runtime/profiles/{profile_id}"
        )
