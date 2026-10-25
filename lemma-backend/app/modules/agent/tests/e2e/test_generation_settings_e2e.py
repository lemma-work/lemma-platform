"""Generation settings reach the provider: per agent, per workflow step, gated.

Everything here runs for real up to the HTTP request: the profile and agent are
created through the API, the run is started the way a workflow step starts it,
and the production runner and harness drive it. The provider is an
OpenAI-compatible server on this computer that records what it was sent --
the only stand-in, and it sits on the far side of the wire, where the claim
under test ("the provider receives reasoning_effort=low") is decided.
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from uuid import UUID, uuid4

import pytest
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.domain.generation import GenerationSettings, ReasoningEffort
from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.config import agent_settings
from app.modules.agent.contracts.workflow_control import (
    build_agent_control_adapter,
)
from app.modules.agent.infrastructure.harness.lemma.harness import PydanticAIHarness
from app.modules.agent.infrastructure.harnesses.registry import HarnessRegistry
from app.modules.agent.infrastructure.models import AgentRunModel, ConversationModel
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.application.background.title import (
    ConversationTitleGenerator,
)
from app.modules.agent.services.run_dispatch import suppress_agent_run_enqueue
from sqlalchemy import select

pytestmark = pytest.mark.e2e

_REASONER = "thinker"
_PLAIN = "talker"


class _Provider:
    """Requests the fake provider received, as their JSON bodies."""

    def __init__(self) -> None:
        self.completions: list[dict[str, object]] = []
        self.base_url = ""
        #: Answer a request carrying ``reasoning_effort`` with a 400, as a
        #: strict endpoint does for a model that does not reason.
        self.refuses_effort = False


def _sse(chunks: list[dict[str, object]]) -> bytes:
    lines = [f"data: {json.dumps(chunk)}\n\n" for chunk in chunks]
    return ("".join(lines) + "data: [DONE]\n\n").encode()


def _answer(body: dict[str, object]) -> tuple[str, bytes]:
    """A reply that ends the run: the final-answer tool when one is offered."""
    tools = body.get("tools") or []
    final = next(
        (
            tool["function"]["name"]
            for tool in tools  # type: ignore[union-attr]
            if "final_answer" in tool["function"]["name"]
        ),
        None,
    )
    usage = {"prompt_tokens": 10, "completion_tokens": 2, "total_tokens": 12}
    if final is not None:
        delta: dict[str, object] = {
            "role": "assistant",
            "tool_calls": [
                {
                    "index": 0,
                    "id": "call_final",
                    "type": "function",
                    "function": {
                        "name": final,
                        "arguments": json.dumps(
                            {"status": "COMPLETED", "output": "ok"}
                        ),
                    },
                }
            ],
        }
        finish = "tool_calls"
    else:
        delta = {"role": "assistant", "content": "Quarterly Plan Review"}
        finish = "stop"
    if not body.get("stream"):
        message = {"role": "assistant", "content": "Quarterly Plan Review"}
        return "application/json", json.dumps(
            {
                "id": "cmpl-1",
                "object": "chat.completion",
                "created": 0,
                "model": body.get("model"),
                "choices": [{"index": 0, "message": message, "finish_reason": "stop"}],
                "usage": usage,
            }
        ).encode()
    base = {"id": "cmpl-1", "object": "chat.completion.chunk", "created": 0}
    return "text/event-stream", _sse(
        [
            {**base, "choices": [{"index": 0, "delta": delta, "finish_reason": None}]},
            {
                **base,
                "choices": [{"index": 0, "delta": {}, "finish_reason": finish}],
                "usage": usage,
            },
        ]
    )


@pytest.fixture
def provider() -> Iterator[_Provider]:
    recorded = _Provider()
    listing = json.dumps({"data": [{"id": _REASONER}, {"id": _PLAIN}]}).encode()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            self._reply("application/json", listing)

        def do_POST(self) -> None:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length) or b"{}")
            recorded.completions.append(body)
            if recorded.refuses_effort and "reasoning_effort" in body:
                error = {"error": {"message": "Unknown field: reasoning_effort"}}
                self._reply("application/json", json.dumps(error).encode(), 400)
                return
            self._reply(*_answer(body))

        def _reply(self, content_type: str, payload: bytes, status: int = 200) -> None:
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    recorded.base_url = f"http://127.0.0.1:{server.server_address[1]}/v1"
    try:
        yield recorded
    finally:
        server.shutdown()
        server.server_close()


@pytest.fixture
def real_model_calls(monkeypatch: pytest.MonkeyPatch) -> None:
    """The real pydantic-ai model, in this process only.

    The worker subprocess stays on the mock model, and nothing here is handed
    to it: runs are started with the enqueue suppressed and driven inline.
    """
    monkeypatch.setattr(settings, "e2e_llm_mode", "real")


async def _pod(client: AsyncClient, org_id: str) -> str:
    response = await client.post(
        "/pods",
        json={
            "name": f"Generation Pod {uuid4().hex[:8]}",
            "organization_id": org_id,
            "type": "HYBRID",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


async def _profile(client: AsyncClient, org_id: str, base_url: str) -> str:
    response = await client.post(
        f"/organizations/{org_id}/agent-runtime/profiles",
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Local provider {uuid4().hex[:8]}",
            "base_url": base_url,
            "api_key": "sk-e2e-not-real",
            "reasoning_model_names": [_REASONER],
        },
    )
    assert response.status_code == 201, response.text
    profile = response.json()
    reasons = {
        entry["name"]: "REASONING" in entry["capabilities"]
        for entry in profile["model_catalog"]
    }
    assert reasons == {_REASONER: True, _PLAIN: False}
    return profile["id"]


async def _agent(
    client: AsyncClient, pod_id: str, *, runtime: dict[str, object]
) -> str:
    name = f"gen_{uuid4().hex[:8]}"
    response = await client.post(
        f"/pods/{pod_id}/agents",
        json={"name": name, "instruction": "Answer briefly.", "agent_runtime": runtime},
    )
    assert response.status_code == 201, response.text
    assert response.json()["agent_runtime"] == runtime
    return name


async def _run_like_a_workflow_step(
    db_session: AsyncSession,
    *,
    pod_id: str,
    user_id: str,
    agent_name: str,
    generation: GenerationSettings | None = None,
    model_name: str | None = None,
) -> UUID:
    with suppress_agent_run_enqueue():
        conversation_id = await build_agent_control_adapter(
            SqlAlchemyUnitOfWork(db_session)
        ).run_agent(
            agent_name=agent_name,
            input_data={"ticket": 7},
            pod_id=UUID(pod_id),
            user_id=UUID(user_id),
            model_name=model_name,
            generation=generation,
        )
    await db_session.commit()
    run_id = await db_session.scalar(
        select(AgentRunModel.id).where(AgentRunModel.conversation_id == conversation_id)
    )
    assert run_id is not None
    runner = AgentRunnerService(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        harness_registry=HarnessRegistry([PydanticAIHarness()]),
    )
    await runner.execute(
        agent_run_id=run_id,
        user_id=UUID(user_id),
        pod_id=UUID(pod_id),
        agent_name=agent_name,
    )
    return conversation_id


@pytest.mark.asyncio
async def test_an_agents_effort_reaches_a_reasoning_model_and_not_a_plain_one(
    authenticated_client: AsyncClient,
    fixed_test_org: dict,
    fixed_test_user: dict,
    db_session: AsyncSession,
    provider: _Provider,
    real_model_calls: None,
) -> None:
    org_id = fixed_test_org["id"]
    pod_id = await _pod(authenticated_client, org_id)
    profile_id = await _profile(authenticated_client, org_id, provider.base_url)
    settings_low = {"reasoning_effort": "low", "max_output_tokens": 777}
    reasoner = await _agent(
        authenticated_client,
        pod_id,
        runtime={
            "profile_id": profile_id,
            "model_name": _REASONER,
            "settings": settings_low,
        },
    )
    plain = await _agent(
        authenticated_client,
        pod_id,
        runtime={
            "profile_id": profile_id,
            "model_name": _PLAIN,
            "settings": settings_low,
        },
    )

    await _run_like_a_workflow_step(
        db_session, pod_id=pod_id, user_id=fixed_test_user["id"], agent_name=reasoner
    )
    to_reasoner = provider.completions[-1]
    await _run_like_a_workflow_step(
        db_session, pod_id=pod_id, user_id=fixed_test_user["id"], agent_name=plain
    )
    to_plain = provider.completions[-1]

    assert to_reasoner["model"] == _REASONER
    assert to_reasoner["reasoning_effort"] == "low"
    assert 777 in (
        to_reasoner.get("max_tokens"),
        to_reasoner.get("max_completion_tokens"),
    )
    assert to_plain["model"] == _PLAIN
    assert "reasoning_effort" not in to_plain
    assert 777 in (to_plain.get("max_tokens"), to_plain.get("max_completion_tokens"))


@pytest.mark.asyncio
async def test_a_workflow_steps_settings_win_and_stay_on_its_conversation(
    authenticated_client: AsyncClient,
    fixed_test_org: dict,
    fixed_test_user: dict,
    db_session: AsyncSession,
    provider: _Provider,
    real_model_calls: None,
) -> None:
    org_id = fixed_test_org["id"]
    pod_id = await _pod(authenticated_client, org_id)
    profile_id = await _profile(authenticated_client, org_id, provider.base_url)
    agent_name = await _agent(
        authenticated_client,
        pod_id,
        runtime={
            "profile_id": profile_id,
            "model_name": _PLAIN,
            "settings": {"reasoning_effort": "low", "temperature": 0.3},
        },
    )

    conversation_id = await _run_like_a_workflow_step(
        db_session,
        pod_id=pod_id,
        user_id=fixed_test_user["id"],
        agent_name=agent_name,
        model_name=_REASONER,
        generation=GenerationSettings(reasoning_effort=ReasoningEffort.HIGH),
    )

    sent = provider.completions[-1]
    assert sent["model"] == _REASONER
    assert sent["reasoning_effort"] == "high"
    # Unset on the step, so still the agent's.
    assert sent["temperature"] == 0.3
    # Recorded where a later run on the same conversation reads it.
    db_session.expire_all()
    conversation = await db_session.get(ConversationModel, conversation_id)
    assert conversation is not None
    expected = {
        "profile_id": profile_id,
        "model_name": _REASONER,
        "settings": {"reasoning_effort": "high", "temperature": 0.3},
    }
    assert conversation.agent_runtime == expected
    run = await db_session.scalar(
        select(AgentRunModel).where(AgentRunModel.conversation_id == conversation_id)
    )
    assert run is not None
    assert run.agent_runtime == expected


@pytest.mark.asyncio
async def test_a_title_asks_every_model_not_to_think_and_retries_a_refusal_plainly(
    provider: _Provider,
    real_model_calls: None,
    fixed_test_org: dict,
    fixed_test_user: dict,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A reasoning model nobody declared thinks at its default for a few words,
    so `reasoning_effort: none` is asked of every model. A strict endpoint that
    refuses the field is asked once more without it, and the title stands."""
    monkeypatch.setenv("LEMMA_OPENAI_BASE_URL", provider.base_url)
    monkeypatch.setattr(agent_settings, "conversation_title_model", None)

    async def title() -> dict[str, object]:
        generated = await ConversationTitleGenerator().generate(
            user_id=UUID(fixed_test_user["id"]),
            organization_id=UUID(fixed_test_org["id"]),
            pod_id=uuid4(),
            user_text="Plan the quarterly review",
            reply_text=None,
        )
        assert generated == "Quarterly Plan Review"
        return provider.completions[-1]

    monkeypatch.setattr(agent_settings, "lemma_openai_reasoning_effort", None)
    monkeypatch.setattr(agent_settings, "lemma_openai_reasoning_model_names", "")
    assert (await title())["reasoning_effort"] == "none"

    monkeypatch.setattr(
        agent_settings, "lemma_openai_reasoning_effort", ReasoningEffort.MEDIUM
    )
    assert (await title())["reasoning_effort"] == "none"

    provider.refuses_effort = True
    asked = len(provider.completions)
    assert "reasoning_effort" not in await title()
    refused, plain = provider.completions[asked:]
    assert refused["reasoning_effort"] == "none" and "reasoning_effort" not in plain


@pytest.mark.asyncio
async def test_a_profile_is_tested_with_and_refused_on_its_saved_settings(
    authenticated_client: AsyncClient,
    fixed_test_org: dict,
    provider: _Provider,
    real_model_calls: None,
) -> None:
    """The Test button sent only an output cap, so a profile whose saved
    settings every run would trip on still reported that it worked."""
    org_id = fixed_test_org["id"]
    profile_id = await _profile(authenticated_client, org_id, provider.base_url)
    path = f"/organizations/{org_id}/agent-runtime/profiles/{profile_id}"

    refused = await authenticated_client.patch(
        path,
        json={"source": "OPENAI_COMPATIBLE", "model_settings": {"temperature": "hot"}},
    )
    assert refused.status_code == 400, refused.text

    saved = await authenticated_client.patch(
        path,
        json={
            "source": "OPENAI_COMPATIBLE",
            "model_settings": {"temperature": 1.5, "reasoning_effort": "minimal"},
        },
    )
    assert saved.status_code == 200, saved.text
    # Kept across the edit: the provider's listing never says which models reason.
    assert {
        entry["name"]
        for entry in saved.json()["model_catalog"]
        if "REASONING" in entry["capabilities"]
    } == {_REASONER}

    tested = await authenticated_client.post(f"{path}/test")
    assert tested.status_code == 200, tested.text
    assert tested.json()["ok"] is True, tested.json()
    sent = provider.completions[-1]
    assert sent["temperature"] == 1.5
    # The profile's default model is the reasoning one, so the effort goes too.
    assert sent["model"] == _REASONER
    assert sent["reasoning_effort"] == "minimal"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "model_settings",
    [{"temperature": "hot"}, {"max_tokens": "unlimited"}],
    ids=["portable-field", "wire-switch"],
)
async def test_settings_no_run_could_use_are_refused_on_create_and_on_edit(
    authenticated_client: AsyncClient,
    fixed_test_org: dict,
    provider: _Provider,
    model_settings: dict[str, object],
) -> None:
    """A portable field and a raw wire switch are both checked before saving:
    a value saved unchecked failed every run on the profile instead."""
    org_id = fixed_test_org["id"]
    profiles = f"/organizations/{org_id}/agent-runtime/profiles"

    created = await authenticated_client.post(
        profiles,
        json={
            "source": "OPENAI_COMPATIBLE",
            "name": f"Refused settings {uuid4().hex[:8]}",
            "base_url": provider.base_url,
            "api_key": "sk-e2e-not-real",
            "model_settings": model_settings,
        },
    )
    assert created.status_code == 400, created.text

    profile_id = await _profile(authenticated_client, org_id, provider.base_url)
    edited = await authenticated_client.patch(
        f"{profiles}/{profile_id}",
        json={"source": "OPENAI_COMPATIBLE", "model_settings": model_settings},
    )
    assert edited.status_code == 400, edited.text
    stored = await authenticated_client.get(f"{profiles}/{profile_id}")
    assert stored.status_code == 200, stored.text
    assert not stored.json().get("model_settings"), stored.json()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "field",
    [
        "name",
        "description",
        "base_url",
        "api_key",
        "default_model_name",
        "model_names",
        "model_settings",
        "reasoning_model_names",
    ],
)
async def test_a_null_in_a_profile_edit_is_answered_not_a_server_error(
    authenticated_client: AsyncClient,
    fixed_test_org: dict,
    provider: _Provider,
    field: str,
) -> None:
    """Every field of the edit is nullable on the wire. A null name or model
    list reached code that only takes a value and answered 500."""
    org_id = fixed_test_org["id"]
    profile_id = await _profile(authenticated_client, org_id, provider.base_url)
    path = f"/organizations/{org_id}/agent-runtime/profiles/{profile_id}"
    before = await authenticated_client.get(path)
    assert before.status_code == 200, before.text

    edited = await authenticated_client.patch(
        path, json={"source": "OPENAI_COMPATIBLE", field: None}
    )

    assert edited.status_code < 500, edited.text
    if field in {"name", "model_names", "reasoning_model_names"}:
        # Nothing to clear them to: null leaves them as they were.
        assert edited.status_code == 200, edited.text
        after = (await authenticated_client.get(path)).json()
        assert after["name"] == before.json()["name"]
        assert after["model_catalog"] == before.json()["model_catalog"]
