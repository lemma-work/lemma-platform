"""Lemma SDK public API.

Exports are resolved lazily (PEP 562). Importing the package — or any submodule
such as a single generated model — no longer eagerly pulls in the HTTP client,
all resource classes, and `requests`. Each name loads its module only on first
access, which keeps `import lemma_sdk` (and the CLI that depends on it) cheap.
"""
from __future__ import annotations

import importlib
from typing import TYPE_CHECKING

_NAME_TO_MODULE = {
    "refresh_cli_session": "auth",
    "Lemma": "client",
    "LemmaAPIError": "errors",
    "LemmaAuthError": "errors",
    "LemmaConfigError": "errors",
    "LemmaConflictError": "errors",
    "LemmaConnectionError": "errors",
    "LemmaError": "errors",
    "LemmaNotFoundError": "errors",
    "LemmaPermissionError": "errors",
    "LemmaRateLimitError": "errors",
    "LemmaServerError": "errors",
    "LemmaTimeoutError": "errors",
    "Pod": "pod",
    "POD_DEFAULT_AGENT_SELECTOR": "resources.conversations",
    "FunctionContext": "runtime",
    "FunctionInput": "types",
    "FunctionOutput": "types",
    "ConnectorPayload": "types",
    "JsonObject": "types",
    "JsonPrimitive": "types",
    "JsonValue": "types",
    "Metadata": "types",
    "RecordData": "types",
}

if TYPE_CHECKING:
    from .auth import refresh_cli_session
    from .client import Lemma
    from .errors import (
        LemmaAPIError,
        LemmaAuthError,
        LemmaConfigError,
        LemmaConflictError,
        LemmaConnectionError,
        LemmaError,
        LemmaNotFoundError,
        LemmaPermissionError,
        LemmaRateLimitError,
        LemmaServerError,
        LemmaTimeoutError,
    )
    from .pod import Pod
    from .resources.conversations import POD_DEFAULT_AGENT_SELECTOR
    from .runtime import FunctionContext
    from .types import (
        FunctionInput,
        FunctionOutput,
        ConnectorPayload,
        JsonObject,
        JsonPrimitive,
        JsonValue,
        Metadata,
        RecordData,
    )


def __getattr__(name: str):
    module = _NAME_TO_MODULE.get(name)
    if module is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    value = getattr(importlib.import_module(f".{module}", __name__), name)
    globals()[name] = value  # cache for subsequent lookups
    return value


def __dir__() -> list[str]:
    return sorted(_NAME_TO_MODULE)


__all__ = [
    "FunctionInput",
    "FunctionContext",
    "FunctionOutput",
    "ConnectorPayload",
    "JsonObject",
    "JsonPrimitive",
    "JsonValue",
    "Lemma",
    "LemmaAPIError",
    "LemmaAuthError",
    "LemmaConfigError",
    "LemmaConflictError",
    "LemmaConnectionError",
    "LemmaError",
    "LemmaNotFoundError",
    "LemmaPermissionError",
    "LemmaRateLimitError",
    "LemmaServerError",
    "LemmaTimeoutError",
    "Metadata",
    "Pod",
    "POD_DEFAULT_AGENT_SELECTOR",
    "RecordData",
    "refresh_cli_session",
]
