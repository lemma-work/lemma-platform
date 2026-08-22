"""Reading a dispatched run's settings out of its runtime profile.

Separated from the harness because these are pure translation: what the
profile says becomes what the run spec carries. The harness is about
driving a run once those are settled.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from app.modules.agent.domain.entities import Conversation
from app.modules.agent.domain.value_objects import HarnessOptions, JsonObject


@dataclass(frozen=True, slots=True)
class AgentHostRunConfig:
    harness_id: UUID
    runtime_profile_id: UUID
    config_selections: JsonObject
    wait_timeout_seconds: int
    model_name: str | None


def agent_host_run_config(options: HarnessOptions) -> AgentHostRunConfig:
    profile = _runtime_profile(options)
    harness_id = UUID(str(profile["harness_id"]))
    runtime_profile_id = UUID(str(profile["profile_id"]))
    config = json_object(profile.get("config"))
    # Read the saved revision so malformed legacy profiles fail before
    # dispatch. Admission intentionally uses the latest live revision after
    # selections are revalidated by the repository.
    str(config["harness_snapshot_revision"])
    # The model comes from the profile snapshot rather than options.model_name,
    # which substitutes a "default" placeholder when nothing is pinned. Agent
    # Host rejects any model the harness does not advertise, so an unpinned
    # profile must send no model and let the harness use its own default.
    raw_model = profile.get("provider_model_name") or profile.get("model_name")
    model_name = str(raw_model).strip() if raw_model else None
    return AgentHostRunConfig(
        harness_id=harness_id,
        runtime_profile_id=runtime_profile_id,
        config_selections=json_object(config.get("config_selections")),
        wait_timeout_seconds=integer(
            config.get("host_wait_timeout_seconds"),
            default=300,
        ),
        model_name=model_name or None,
    )


def resolve_pod_cwd(conversation: Conversation) -> str:
    from app.modules.agent.services.workspace_location import resolve_pod_cwd

    return resolve_pod_cwd(conversation)


def _runtime_profile(options: HarnessOptions) -> JsonObject:
    extra = getattr(options, "extra", None)
    profile = json_object(extra).get("runtime_profile") if extra else None
    if not isinstance(profile, dict):
        raise ValueError("runtime profile is missing from harness options")
    return profile


def json_object(value: object) -> JsonObject:
    return dict(value) if isinstance(value, dict) else {}


def integer(value: object, *, default: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | float | str):
        return default
    try:
        return int(value)
    except ValueError:
        return default


def joined_prompt(prompt: JsonObject) -> str:
    parts = [
        str(prompt.get(key) or "")
        for key in ("system_prompt", "recovery_system_prompt", "user_prompt")
    ]
    return "\n\n".join(part for part in parts if part)
