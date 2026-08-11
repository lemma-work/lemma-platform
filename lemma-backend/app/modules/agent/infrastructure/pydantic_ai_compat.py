"""The one place the agent module reaches into pydantic-ai internals.

Everything re-exported here lives under a leading underscore in pydantic-ai and
carries no stability guarantee. Importing it from a single module means a
pydantic-ai upgrade breaks in one file with a clear message, instead of in five
call sites with five different tracebacks.

The version assertion below is deliberately a warning rather than a hard error:
a patch bump that happens to move one of these should surface loudly in the logs
without taking the API down at import time. The upper bound in ``pyproject.toml``
is what actually stops an unplanned major upgrade.

If pydantic-ai ever grows public equivalents, delete the corresponding re-export
here and import the public name directly.
"""

from __future__ import annotations

from pydantic_ai import __version__ as _pydantic_ai_version

# Private schema plumbing. ``InlineDefsJsonSchemaTransformer`` inlines
# ``$defs``/``$ref`` for providers that cannot resolve references server-side
# (see ``services/openai_schema_compat``); ``FunctionSchema`` is how dynamically
# built tools (``function_*``/``agent_*``) declare their signature.
from pydantic_ai._json_schema import (  # noqa: PLC2701
    InlineDefsJsonSchemaTransformer,
    JsonSchema,
    JsonSchemaTransformer,
)
from pydantic_ai._function_schema import FunctionSchema  # noqa: PLC2701

# Only used as a type annotation, by the current-time capability's
# ``before_model_request`` hook.
from pydantic_ai._agent_graph import ModelRequestContext  # noqa: PLC2701

__all__ = [
    "FunctionSchema",
    "InlineDefsJsonSchemaTransformer",
    "JsonSchema",
    "JsonSchemaTransformer",
    "ModelRequestContext",
    "SUPPORTED_PYDANTIC_AI_MAJOR",
]

SUPPORTED_PYDANTIC_AI_MAJOR = 2


def _check_version() -> None:
    major, _, rest = _pydantic_ai_version.partition(".")
    del rest
    try:
        installed_major = int(major)
    except ValueError:  # pragma: no cover - non-numeric dev version
        return
    if installed_major != SUPPORTED_PYDANTIC_AI_MAJOR:
        from app.core.log.log import get_logger

        get_logger(__name__).warning(
            "agent.pydantic_ai_compat.unsupported_major_version.degraded",
            installed_version=_pydantic_ai_version,
            supported_major=SUPPORTED_PYDANTIC_AI_MAJOR,
        )


_check_version()
