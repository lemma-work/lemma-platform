from __future__ import annotations

from typing import Literal

from pydantic import BaseModel


class SandboxAppSpec(BaseModel):
    name: str
    public_slug: str
    port: int
    health_path: str = "/health"
    startup: Literal["eager", "lazy"] = "lazy"
    exposure: Literal["private", "workspace_user"] = "private"
    auth_mode: Literal["manager_api_key", "workspace_access_token"] = "manager_api_key"


SANDBOX_APPS: dict[str, SandboxAppSpec] = {
    "runtime": SandboxAppSpec(
        name="runtime",
        public_slug="runtime",
        port=8080,
        startup="eager",
        exposure="private",
        auth_mode="manager_api_key",
    ),
    "browser": SandboxAppSpec(
        name="browser",
        public_slug="browser",
        port=4848,
        startup="lazy",
        exposure="workspace_user",
        auth_mode="workspace_access_token",
    ),
    "function_executor": SandboxAppSpec(
        name="function_executor",
        public_slug="function",
        port=8090,
        # start-runtime launches this process unconditionally. Treat it as an
        # eager app so providers do not publish a ready sandbox while port 8090
        # is still binding and reset the first function invocation burst.
        startup="eager",
        exposure="private",
        auth_mode="manager_api_key",
    ),
}


SANDBOX_APPS_BY_SLUG: dict[str, SandboxAppSpec] = {
    spec.public_slug: spec for spec in SANDBOX_APPS.values()
}


def sandbox_app(name: str) -> SandboxAppSpec:
    try:
        return SANDBOX_APPS[name]
    except KeyError as exc:
        raise ValueError(f"Unknown sandbox app: {name}") from exc


def sandbox_app_from_slug(slug: str) -> SandboxAppSpec:
    try:
        return SANDBOX_APPS_BY_SLUG[slug]
    except KeyError as exc:
        raise ValueError(f"Unknown sandbox app slug: {slug}") from exc
