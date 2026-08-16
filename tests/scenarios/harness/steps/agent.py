"""What a person does with agents and conversations."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

from harness.drivers.api import items_of
from harness.waiting import eventually

JSON = dict[str, Any]

#: A run has finished when it reaches one of these. Anything else means it is
#: still going, and a scenario asserting on the answer has to keep waiting.
SETTLED = {"COMPLETED", "FAILED", "STOPPED", "CANCELLED", "WAITING_FOR_INPUT"}


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

    async def grants_of_agent(self, name: str, *, in_pod: JSON) -> JSON:
        return await self.api.get(f"/pods/{in_pod['id']}/agents/{name}/permissions")

    # --- conversations ---------------------------------------------------

    async def starts_a_conversation(
        self, *, in_pod: JSON, with_agent: str | None = None, saying: str | None = None
    ) -> JSON:
        """Open a thread, and optionally say the first thing.

        Creating a conversation and sending a message are two calls: the create
        body has no message field, so passing one is silently ignored and the
        agent is never asked anything. Bundling them here means a scenario says
        "starts a conversation saying X" and gets what it asked for.
        """
        body: JSON = {}
        if with_agent:
            body["agent_name"] = with_agent
        conversation = await self.api.post(
            f"/pods/{in_pod['id']}/conversations",
            what=f"{self.label} starting a conversation in {in_pod.get('name')!r}",
            json=body,
        )
        self.conversation = conversation
        if saying:
            await self.says(saying, in_conversation=conversation, in_pod=in_pod)
        return conversation

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
