from __future__ import annotations

import os
from dataclasses import dataclass, field


def _required(name: str, provider: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise RuntimeError(f"{name} is required for the {provider} provider")
    return value


@dataclass(frozen=True)
class E2BProviderConfig:
    api_key: str = field(repr=False)
    template: str
    max_active: int = 10
    timeout_seconds: int = 3600
    api_url: str | None = None
    domain: str | None = None
    owner: str = "agentbox"
    allow_internet_access: bool = True
    admission_wait_seconds: float = 60.0
    capacity_retry_after_seconds: int = 15
    create_rate_per_second: float = 1.0
    create_max_in_flight: int = 3
    runtime_user: str = "10001"
    runtime_bootstrap_timeout_seconds: float = 30.0

    @classmethod
    def from_env(cls) -> "E2BProviderConfig":
        max_active = int(os.environ.get("E2B_SANDBOX_MAX_ACTIVE", "10"))
        if not 1 <= max_active <= 100:
            raise RuntimeError("E2B_SANDBOX_MAX_ACTIVE must be between 1 and 100")
        timeout = int(os.environ.get("E2B_SANDBOX_TIMEOUT_SECONDS", "3600"))
        if not 60 <= timeout <= 3600:
            raise RuntimeError(
                "E2B_SANDBOX_TIMEOUT_SECONDS must be between 60 and 3600"
            )
        admission_wait = float(
            os.environ.get("E2B_SANDBOX_ADMISSION_WAIT_SECONDS", "60")
        )
        capacity_retry_after = int(
            os.environ.get("E2B_SANDBOX_RETRY_AFTER_SECONDS", "15")
        )
        create_rate = float(
            os.environ.get("E2B_SANDBOX_CREATE_RATE_PER_SECOND", "1")
        )
        create_max_in_flight = int(
            os.environ.get("E2B_SANDBOX_CREATE_MAX_IN_FLIGHT", "3")
        )
        if not 0 < admission_wait <= 600:
            raise RuntimeError(
                "E2B_SANDBOX_ADMISSION_WAIT_SECONDS must be greater than 0 "
                "and at most 600"
            )
        if not 1 <= capacity_retry_after <= 300:
            raise RuntimeError(
                "E2B_SANDBOX_RETRY_AFTER_SECONDS must be between 1 and 300"
            )
        if not 0 < create_rate <= 100:
            raise RuntimeError(
                "E2B_SANDBOX_CREATE_RATE_PER_SECOND must be between 0 and 100"
            )
        if not 1 <= create_max_in_flight <= 100:
            raise RuntimeError(
                "E2B_SANDBOX_CREATE_MAX_IN_FLIGHT must be between 1 and 100"
            )
        return cls(
            api_key=_required("E2B_API_KEY", "E2B"),
            template=_required("E2B_SANDBOX_TEMPLATE", "E2B"),
            max_active=max_active,
            timeout_seconds=timeout,
            api_url=os.environ.get("E2B_API_URL", "").strip() or None,
            domain=os.environ.get("E2B_DOMAIN", "").strip() or None,
            owner=os.environ.get("AGENTBOX_PROVIDER_OWNER", "agentbox").strip()
            or "agentbox",
            allow_internet_access=os.environ.get(
                "E2B_ALLOW_INTERNET_ACCESS", "true"
            ).lower()
            in {"1", "true", "yes"},
            admission_wait_seconds=admission_wait,
            capacity_retry_after_seconds=capacity_retry_after,
            create_rate_per_second=create_rate,
            create_max_in_flight=create_max_in_flight,
            runtime_user=os.environ.get("E2B_RUNTIME_USER", "10001"),
            runtime_bootstrap_timeout_seconds=float(
                os.environ.get("E2B_RUNTIME_BOOTSTRAP_TIMEOUT_SECONDS", "30")
            ),
        )


@dataclass(frozen=True)
class DaytonaProviderConfig:
    api_key: str = field(repr=False)
    snapshot: str | None = None
    image: str | None = None
    api_url: str = "https://app.daytona.io/api"
    target: str | None = None
    max_active: int = 10
    auto_stop_minutes: int = 3
    auto_delete_minutes: int = 1440
    owner: str = "agentbox"
    ready_timeout_seconds: float = 120.0
    admission_wait_seconds: float = 60.0
    capacity_retry_after_seconds: int = 15
    create_rate_per_second: float = 1.0
    create_max_in_flight: int = 3

    @classmethod
    def from_env(cls) -> "DaytonaProviderConfig":
        snapshot = os.environ.get("DAYTONA_SANDBOX_SNAPSHOT", "").strip() or None
        image = os.environ.get("DAYTONA_SANDBOX_IMAGE", "").strip() or None
        if bool(snapshot) == bool(image):
            raise RuntimeError(
                "exactly one of DAYTONA_SANDBOX_SNAPSHOT or "
                "DAYTONA_SANDBOX_IMAGE is required"
            )
        max_active = int(os.environ.get("DAYTONA_SANDBOX_MAX_ACTIVE", "10"))
        if not 1 <= max_active <= 100:
            raise RuntimeError(
                "DAYTONA_SANDBOX_MAX_ACTIVE must be between 1 and 100"
            )
        auto_stop = int(os.environ.get("DAYTONA_SANDBOX_AUTO_STOP_MINUTES", "3"))
        auto_delete = int(
            os.environ.get("DAYTONA_SANDBOX_AUTO_DELETE_MINUTES", "1440")
        )
        if auto_stop < 1 or auto_delete < auto_stop:
            raise RuntimeError("Daytona auto-delete must not precede auto-stop")
        admission_wait = float(
            os.environ.get("DAYTONA_SANDBOX_ADMISSION_WAIT_SECONDS", "60")
        )
        retry_after = int(
            os.environ.get("DAYTONA_SANDBOX_RETRY_AFTER_SECONDS", "15")
        )
        create_rate = float(
            os.environ.get("DAYTONA_SANDBOX_CREATE_RATE_PER_SECOND", "1")
        )
        create_max_in_flight = int(
            os.environ.get("DAYTONA_SANDBOX_CREATE_MAX_IN_FLIGHT", "3")
        )
        if not 0 < admission_wait <= 600:
            raise RuntimeError(
                "DAYTONA_SANDBOX_ADMISSION_WAIT_SECONDS must be in (0, 600]"
            )
        if not 1 <= retry_after <= 300:
            raise RuntimeError(
                "DAYTONA_SANDBOX_RETRY_AFTER_SECONDS must be between 1 and 300"
            )
        if not 0 < create_rate <= 100:
            raise RuntimeError(
                "DAYTONA_SANDBOX_CREATE_RATE_PER_SECOND must be in (0, 100]"
            )
        if not 1 <= create_max_in_flight <= 100:
            raise RuntimeError(
                "DAYTONA_SANDBOX_CREATE_MAX_IN_FLIGHT must be between 1 and 100"
            )
        return cls(
            api_key=_required("DAYTONA_API_KEY", "Daytona"),
            snapshot=snapshot,
            image=image,
            api_url=os.environ.get(
                "DAYTONA_API_URL", "https://app.daytona.io/api"
            ).rstrip("/"),
            target=os.environ.get("DAYTONA_TARGET", "").strip() or None,
            max_active=max_active,
            auto_stop_minutes=auto_stop,
            auto_delete_minutes=auto_delete,
            owner=os.environ.get("AGENTBOX_PROVIDER_OWNER", "agentbox").strip()
            or "agentbox",
            ready_timeout_seconds=float(
                os.environ.get("DAYTONA_READY_TIMEOUT_SECONDS", "120")
            ),
            admission_wait_seconds=admission_wait,
            capacity_retry_after_seconds=retry_after,
            create_rate_per_second=create_rate,
            create_max_in_flight=create_max_in_flight,
        )
