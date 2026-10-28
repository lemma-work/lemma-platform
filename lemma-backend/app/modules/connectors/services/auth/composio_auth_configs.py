"""Composio auth configs, made once per toolkit and options.

Every connect used to create a new Composio auth config -- a provider round
trip each time, and one more config left in the Composio project per click.
They are remembered instead, keyed by the toolkit and a digest of the options.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from typing import Callable, Protocol


class AuthConfigIdCache(Protocol):
    """Where a Composio auth config id is remembered; misses on an outage."""

    async def get(self, suffix: str, decode: Callable[[str], str]) -> str | None: ...

    async def set(self, suffix: str, payload: str) -> None: ...

    async def delete(self, suffix: str) -> None: ...


@dataclass(frozen=True, slots=True)
class ConnectionStart:
    """What a started sign-in returns: the connection's id and where to go."""

    id: str
    redirect_url: str | None


def auth_config_key(toolkit: str, options: dict[str, object]) -> str:
    """The toolkit and a digest of the options -- never the options, which
    carry an organization's OAuth client secret for a custom install."""
    canonical = json.dumps(options, sort_keys=True, separators=(",", ":"), default=str)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()[:32]
    return f"{toolkit}:{digest}"


__all__ = ["AuthConfigIdCache", "ConnectionStart", "auth_config_key"]
