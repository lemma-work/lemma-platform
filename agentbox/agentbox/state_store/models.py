from __future__ import annotations

from dataclasses import dataclass

from agentbox.schemas import SandboxEnsureRequest


@dataclass(frozen=True)
class SandboxRecord:
    sandbox_id: str
    env: dict[str, str]
    desired_state: str = "present"
    desired_generation: int = 1
    observed_generation: int = 0
    provider_name: str | None = None
    provider_id: str | None = None
    instance_id: str | None = None
    last_active_at: float | None = None
    last_observed_at: float | None = None

    def to_ensure_request(self) -> SandboxEnsureRequest:
        return SandboxEnsureRequest(env=self.env)


@dataclass(frozen=True)
class SessionRecord:
    sandbox_id: str
    session_id: str
    cwd: str
    env_keys: list[str]
    last_active_at: float
    active_operations: int


@dataclass(frozen=True)
class ActivityLease:
    lease_id: str
    sandbox_id: str
    session_id: str | None
    operation: str
    owner: str
    expires_at: float


@dataclass(frozen=True)
class LifecycleClaim:
    claim_id: str
    sandbox_id: str
    operation: str
    owner: str
    expires_at: float


@dataclass(frozen=True)
class OrphanCandidate:
    provider_name: str
    provider_id: str
    sandbox_id: str | None
    first_seen_at: float
    last_seen_at: float
