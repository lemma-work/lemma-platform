"""Run sample local agent chats and save runtime events to logs.

This script uses the real agent services and harnesses directly against the
configured local database. It does not require an HTTP auth cookie.

Example:
    uv run python scripts/sample_local_agent_chat.py
"""

# ruff: noqa: E402

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from types import MethodType
from typing import Any
from uuid import UUID

from sqlalchemy import func, select

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from app.core.helpers.slug import normalize_resource_name
from app.core.infrastructure.db.session import async_session_maker, close_engine
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.events import (
    AgentRunCompletedEvent,
    AgentRunErrorEvent,
    AgentRunMessageEvent,
    AgentRunTokenEvent,
)
from app.modules.agent.domain.value_objects import (
    AgentRuntimeConfig,
    HarnessKind,
    MessageRole,
)
from app.modules.agent.events.handlers import build_harness_registry
from app.modules.agent.infrastructure.repositories import (
    AgentRepository,
    ConversationRepository,
)
from app.modules.agent.services.agent_runner_service import AgentRunnerService
from app.modules.agent.services.agent_service import AgentService
from app.modules.agent.services.serialization import message_to_payload
from app.modules.identity.infrastructure.models.user_models import User
from app.modules.pod.infrastructure.models.pod_models import Pod

DEFAULT_USER_EMAIL = "lemma@lemma.work"
DEFAULT_POD_NAME = "local-test"
DEFAULT_AGENT_NAME = "sample_local_agent"
DEFAULT_MODEL_NAME = "KIMI_K2"
DEFAULT_AGENT_QUESTION = (
    "Give me a concise status-style answer: who are you, which pod are you "
    "running inside, and what is one useful next step you can help with?"
)
DEFAULT_ASSISTANT_QUESTION = (
    "Give me a concise status-style answer as the default pod assistant: what "
    "can you help me do in this pod?"
)
DEFAULT_AGENT_INSTRUCTION = """\
You are Sample Local Agent, a small test agent for validating Lemma agent runs.
Answer clearly and briefly. Mention when you are running as the named sample
agent so the caller can distinguish you from the default pod assistant.
"""


class ScriptError(RuntimeError):
    """Expected script failure with a user-readable message."""


def _json_default(value: object) -> str:
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    return str(value)


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _runtime_payload(event: object) -> dict[str, Any]:
    if isinstance(event, AgentRunTokenEvent):
        return {
            "type": "token",
            "agent_run_id": str(event.agent_run_id),
            "data": event.token,
            "event": event.model_dump(mode="json"),
        }
    if isinstance(event, AgentRunMessageEvent):
        return {
            "type": "message",
            "agent_run_id": str(event.agent_run_id),
            "data": event.message,
            "event": event.model_dump(mode="json"),
        }
    if isinstance(event, AgentRunErrorEvent):
        return {
            "type": "error",
            "agent_run_id": str(event.agent_run_id),
            "data": event.error,
            "event": event.model_dump(mode="json"),
        }
    if isinstance(event, AgentRunCompletedEvent):
        return {
            "type": "completed",
            "agent_run_id": str(event.agent_run_id),
            "data": {
                "conversation_id": str(event.conversation_id),
                "status": event.status.value,
                **(event.data or {}),
            },
            "event": event.model_dump(mode="json"),
        }
    event_type = getattr(event, "event_type", type(event).__name__)
    return {
        "type": str(event_type),
        "data": event.model_dump(mode="json") if hasattr(event, "model_dump") else str(event),
    }


def _final_text(messages: list[dict[str, Any]]) -> str | None:
    for message in reversed(messages):
        if message.get("role") != MessageRole.ASSISTANT.value:
            continue
        content = message.get("content")
        if isinstance(content, dict) and content.get("type") == "text":
            value = content.get("content")
            return str(value) if value is not None else None
    return None


@asynccontextmanager
async def _uow_context():
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        try:
            yield uow
        except BaseException:
            await uow.rollback()
            raise
        else:
            await uow.commit()


async def _resolve_user_and_pod(
    *,
    user_email: str,
    pod_name: str,
) -> tuple[User, Pod]:
    async with async_session_maker() as session:
        user_result = await session.execute(
            select(User).where(func.lower(User.email) == user_email.lower())
        )
        user = user_result.scalar_one_or_none()
        if user is None:
            raise ScriptError(f"User '{user_email}' was not found in the local DB.")

        pod_result = await session.execute(
            select(Pod).where(
                func.lower(Pod.name) == pod_name.lower(),
                Pod.is_deleted.is_(False),
            )
        )
        pods = list(pod_result.scalars())
        if not pods:
            raise ScriptError(f"Pod '{pod_name}' was not found in the local DB.")

        owned_pods = [pod for pod in pods if pod.user_id == user.id]
        return user, (owned_pods[0] if owned_pods else pods[0])


async def _upsert_agent(
    *,
    user_id: UUID,
    pod_id: UUID,
    agent_name: str,
    instruction: str,
    model_name: str,
) -> tuple[dict[str, Any], bool]:
    normalized_name = normalize_resource_name(agent_name)
    agent_runtime = AgentRuntimeConfig(
        harness_kind=HarnessKind.LEMMA,
        model_name=model_name,
    )
    async with _uow_context() as uow:
        service = AgentService(agent_repository=AgentRepository(uow))
        existing = await service.agent_repository.get_by_pod_and_name(
            pod_id=pod_id,
            name=normalized_name,
        )
        if existing is None:
            agent = await service.create_agent(
                pod_id=pod_id,
                user_id=user_id,
                name=normalized_name,
                description="Created by scripts/sample_local_agent_chat.py",
                instruction=instruction,
                agent_runtime=agent_runtime,
                toolsets=[],
                metadata={"source": "sample_local_agent_chat"},
            )
            return agent.model_dump(mode="json"), True

        agent = await service.update_agent(
            pod_id=pod_id,
            name=normalized_name,
            description="Updated by scripts/sample_local_agent_chat.py",
            instruction=instruction,
            agent_runtime=agent_runtime,
            toolsets=[],
            metadata={"source": "sample_local_agent_chat"},
        )
        return agent.model_dump(mode="json"), False


async def _create_run_seed(
    *,
    user_id: UUID,
    pod: Pod,
    agent_id: UUID | None,
    model_name: str,
    question: str,
    label: str,
) -> tuple[UUID, UUID]:
    agent_runtime = AgentRuntimeConfig(
        harness_kind=HarnessKind.LEMMA,
        model_name=model_name,
    )
    async with _uow_context() as uow:
        repo = ConversationRepository(uow)
        conversation = await repo.create_conversation(
            Conversation(
                user_id=user_id,
                pod_id=pod.id,
                organization_id=pod.organization_id,
                agent_id=agent_id,
                title=f"Sample script chat: {label}",
                agent_runtime=agent_runtime,
                metadata={
                    "source": "sample_local_agent_chat",
                    "label": label,
                },
            )
        )
        run = await repo.create_agent_run(
            conversation_id=conversation.id,
            agent_id=agent_id,
            agent_runtime=agent_runtime,
            metadata={"source": "sample_local_agent_chat", "label": label},
        )
        await repo.append_message(
            conversation_id=conversation.id,
            agent_run_id=run.id,
            role=MessageRole.USER.value,
            content=question,
            metadata={
                "author_user_id": str(user_id),
                "source": "sample_local_agent_chat",
            },
        )
        return conversation.id, run.id


async def _load_result(
    *,
    conversation_id: UUID,
    agent_run_id: UUID,
) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    async with async_session_maker() as session:
        uow = SqlAlchemyUnitOfWork(session)
        repo = ConversationRepository(uow)
        run = await repo.get_agent_run(agent_run_id)
        messages, _ = await repo.list_messages(
            conversation_id=conversation_id,
            limit=500,
        )
        return (
            run.model_dump(mode="json") if run is not None else None,
            [message_to_payload(message) for message in messages],
        )


async def _run_chat(
    *,
    user: User,
    pod: Pod,
    agent: dict[str, Any] | None,
    model_name: str,
    question: str,
    label: str,
    logs_dir: Path,
) -> Path:
    agent_id = UUID(str(agent["id"])) if agent is not None else None
    agent_name = str(agent["name"]) if agent is not None else None
    conversation_id, agent_run_id = await _create_run_seed(
        user_id=user.id,
        pod=pod,
        agent_id=agent_id,
        model_name=model_name,
        question=question,
        label=label,
    )

    runtime_events: list[dict[str, Any]] = []

    runner = AgentRunnerService(
        uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        harness_registry=build_harness_registry(),
        fallback_model_name=model_name,
    )

    async def capture_runtime_event(self, event: object) -> None:
        _ = self
        runtime_events.append(_runtime_payload(event))

    runner._publish_runtime_event = MethodType(capture_runtime_event, runner)
    await runner.execute(
        agent_run_id=agent_run_id,
        user_id=user.id,
        pod_id=pod.id,
        agent_name=agent_name,
    )

    run_payload, messages = await _load_result(
        conversation_id=conversation_id,
        agent_run_id=agent_run_id,
    )
    tokens = [
        str(event.get("data"))
        for event in runtime_events
        if event.get("type") == "token"
    ]
    result = {
        "script": "scripts/sample_local_agent_chat.py",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "label": label,
        "user": {"id": str(user.id), "email": user.email},
        "pod": {"id": str(pod.id), "name": pod.name},
        "agent": agent,
        "conversation_id": str(conversation_id),
        "agent_run_id": str(agent_run_id),
        "agent_runtime": {
            "harness_kind": HarnessKind.LEMMA.value,
            "model_name": model_name,
        },
        "question": question,
        "stream_events": runtime_events,
        "tokens": tokens,
        "token_text": "".join(tokens),
        "messages": messages,
        "final_text": _final_text(messages),
        "agent_run": run_payload,
    }

    logs_dir.mkdir(parents=True, exist_ok=True)
    output_path = logs_dir / f"{_timestamp()}_{label}.json"
    output_path.write_text(
        json.dumps(result, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return output_path


async def _async_main(args: argparse.Namespace) -> None:
    model_name = args.model_name
    user, pod = await _resolve_user_and_pod(
        user_email=args.user_email,
        pod_name=args.pod_name,
    )
    agent, created = await _upsert_agent(
        user_id=user.id,
        pod_id=pod.id,
        agent_name=args.agent_name,
        instruction=args.agent_instruction,
        model_name=model_name,
    )
    verb = "created" if created else "updated"
    print(f"{verb} agent {agent['name']} ({agent['id']}) in pod {pod.name} ({pod.id})")

    named_agent_log = await _run_chat(
        user=user,
        pod=pod,
        agent=agent,
        model_name=model_name,
        question=args.agent_question,
        label=f"agent_{agent['name']}",
        logs_dir=args.logs_dir,
    )
    print(f"wrote named-agent chat log: {named_agent_log}")

    default_agent_log = await _run_chat(
        user=user,
        pod=pod,
        agent=None,
        model_name=model_name,
        question=args.assistant_question,
        label="default_pod_agent",
        logs_dir=args.logs_dir,
    )
    print(f"wrote default pod agent chat log: {default_agent_log}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description=(
            "Create/update a sample local agent, run one named-agent chat and "
            "one default pod-agent chat, and save runtime events/messages as JSON."
        )
    )
    parser.add_argument("--user-email", default=DEFAULT_USER_EMAIL)
    parser.add_argument("--pod-name", default=DEFAULT_POD_NAME)
    parser.add_argument("--agent-name", default=DEFAULT_AGENT_NAME)
    parser.add_argument(
        "--model-name",
        default=DEFAULT_MODEL_NAME,
    )
    parser.add_argument("--agent-question", default=DEFAULT_AGENT_QUESTION)
    parser.add_argument("--assistant-question", default=DEFAULT_ASSISTANT_QUESTION)
    parser.add_argument("--agent-instruction", default=DEFAULT_AGENT_INSTRUCTION)
    parser.add_argument(
        "--logs-dir",
        type=Path,
        default=Path("logs"),
        help="Directory where JSON chat logs will be written.",
    )
    return parser.parse_args()


def main() -> None:
    args = _parse_args()
    try:
        asyncio.run(_async_main(args))
    except ScriptError as exc:
        raise SystemExit(str(exc)) from exc
    finally:
        asyncio.run(close_engine())


if __name__ == "__main__":
    main()
