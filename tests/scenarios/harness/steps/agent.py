"""What a person does with agents and conversations."""

from __future__ import annotations

import asyncio
import json
import time
from typing import Any
from uuid import uuid4

from harness.drivers.api import items_of
from harness.waiting import eventually

JSON = dict[str, Any]

#: A run has finished when it reaches one of these. Anything else means it is
#: still going, and a scenario asserting on the answer has to keep waiting.
SETTLED = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED", "WAITING_FOR_INPUT"}

#: Where the deterministic model reads its turns from. The stack boots with
#: `E2E_LLM_MODE=mock` (chosen at boot, like the sandbox provider), and that
#: model takes its script from the conversation's own `metadata` — a documented
#: field on `agent.conversation.create`.
#:
#: So a scenario decides what an agent *attempts* by passing product data
#: through the public API, not by patching a model. This is the same posture as
#: pointing a surface at a self-hosted Bot API server with `api_base_url`: the
#: seam is a supported product capability, and Lemma's own authorization, tool
#: dispatch, approval and persistence all run for real. Without it, an agent
#: scenario can only prove that *something* was answered — never that a
#: destructive action was refused, because an unscripted model never tries one.
SCRIPT_KEY = "mock_llm_script"


def attempts(
    tool: str, /, *, remembered_as: str | None = None, **arguments: Any
) -> JSON:
    """One turn in which the agent tries to call ``tool``.

    Tool names are the agent's real ones — `pod_write_record`, `pod_query`,
    `run_connector_operation`. A name no toolset exposes fails the run, which is
    the honest outcome rather than a silent no-op.

    ``remembered_as`` names this call so a later turn can quote part of its
    result with :func:`result_of`.
    """
    return {
        "tool_calls": [
            {
                "tool_name": tool,
                "args": arguments,
                "tool_call_id": remembered_as or f"call_{uuid4().hex[:8]}",
            }
        ]
    }


def result_of(call: str, path: str) -> str:
    """Quote part of an earlier tool's result inside a later turn's arguments.

    A script is static JSON, so it cannot contain a value the run only produces
    at runtime — and the interesting arguments are exactly those. The
    deterministic model resolves ``${call.dotted.path}`` against the result of
    the named earlier call.

    This is what makes an approval scenario honest. A real agent learns which
    permissions it was denied from the failed call's own `approval` envelope and
    quotes them back; a scenario that hard-codes them instead would prove the
    plumbing works on values no agent could have known.
    """
    return f"${{{call}.{path}}}"


def answers(text: str) -> JSON:
    """One turn in which the agent simply says something and stops."""
    return {"text": text}


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
        name = named or f"agent_{uuid4().hex[:10]}"
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

    async def agents_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/agents"))

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
        where_the_agent: list[JSON] | None = None,
        under: JSON | None = None,
    ) -> JSON:
        """Open a thread, and optionally say the first thing.

        Creating a conversation and sending a message are two calls: the create
        body has no message field, so passing one is silently ignored and the
        agent is never asked anything. Bundling them here means a scenario says
        "starts a conversation saying X" and gets what it asked for.

        ``where_the_agent`` is what the agent will try, turn by turn — see
        :func:`attempts` and :data:`SCRIPT_KEY`. ``under`` makes this thread a
        child of another, which is how a subagent's work stays attached to the
        conversation that delegated it.
        """
        body: JSON = {}
        if with_agent:
            body["agent_name"] = with_agent
        if where_the_agent is not None:
            body["metadata"] = {SCRIPT_KEY: where_the_agent}
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

    async def tells_the_agent_to(
        self, conversation: JSON, turns: list[JSON], *, in_pod: JSON
    ) -> JSON:
        """Script a conversation this scenario did not create.

        A thread opened by a surface belongs to the surface, so there is no
        create call to pass `metadata` to. `agent.conversation.update` takes
        metadata, which is how a scenario reaches a conversation that arrived
        from outside.
        """
        return await self.api.patch(
            f"/pods/{in_pod['id']}/conversations/{conversation['id']}",
            what=f"{self.label} deciding what the agent will attempt",
            json={"metadata": {SCRIPT_KEY: turns}},
        )

    async def conversations_in(self, pod: JSON) -> list[JSON]:
        return items_of(await self.api.get(f"/pods/{pod['id']}/conversations"))

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

    async def messages_in(self, conversation: JSON, *, in_pod: JSON) -> list[JSON]:
        return items_of(
            await self.api.get(
                f"/pods/{in_pod['id']}/conversations/{conversation['id']}/messages"
            )
        )

    async def waits_for_a_reply(
        self, *, in_conversation: JSON, in_pod: JSON, after: int = 0, timeout: float = 60.0
    ) -> list[JSON]:
        """Wait until the agent has added at least one message beyond ``after``.

        Runs are queued to the worker, so the reply is not in the response to
        sending the message. Polling the transcript is exactly what a client
        without a stream does.
        """
        return await eventually(
            lambda: self.messages_in(in_conversation, in_pod=in_pod),
            lambda messages: len(messages) > after
            and any(m.get("role") == "assistant" for m in messages),
            describe=(
                f"the agent to reply in conversation {in_conversation['id']}"
            ),
            timeout=timeout,
        )

    async def waits_for_the_run_to_settle(
        self, *, conversation: JSON, in_pod: JSON, timeout: float = 60.0
    ) -> JSON:
        return await eventually(
            lambda: self.opens_conversation(conversation, in_pod=in_pod),
            lambda payload: str(payload.get("status") or "").upper() in SETTLED
            or payload.get("status") is None,
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
        self, conversation: JSON, *, in_pod: JSON, after: int = 0, timeout: float = 60.0
    ) -> list[JSON]:
        """Wait until the run has asked a person for permission."""
        return await eventually(
            lambda: self.approvals_in(conversation, in_pod=in_pod),
            lambda requests: len(requests) > after,
            describe=f"an approval request in conversation {conversation['id']}",
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
            if str(state.get("status") or "").upper() in SETTLED:
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

    async def opens_runtime_profile(self, profile_id: str, *, in_organization: JSON) -> JSON:
        return await self.api.get(
            f"/organizations/{in_organization['id']}/agent-runtime/profiles/{profile_id}"
        )
