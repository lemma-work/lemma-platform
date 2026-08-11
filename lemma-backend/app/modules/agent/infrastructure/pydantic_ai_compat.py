"""The one place the agent module reaches into pydantic-ai internals.

Everything re-exported here lives under a leading underscore in pydantic-ai and
carries no stability guarantee. Importing it from a single module means a
pydantic-ai upgrade breaks in one file with a clear message, instead of in five
call sites with five different tracebacks.

The version assertion below is a hard error on a *major* mismatch, and it used
to be a warning. That was wrong, and dev proved it: an image shipped carrying
this code against pydantic-ai 1.107, the warning scrolled past in the logs, and
the service came up and served traffic on an agent stack whose public API it was
not written for. A major mismatch is not a degraded mode — the streaming
capabilities API, model profiles and tool deferral all changed shape across 2.0 —
so the honest failure is to refuse to start. A container that will not boot is a
five-minute rollback; one that boots and answers subtly wrong is a day.

Minor and patch drift stays silent: the upper bound in ``pyproject.toml`` is what
governs there, and this module's imports would fail on their own if one of the
private names actually moved.

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
    "UnsupportedPydanticAIVersion",
    "check_pydantic_ai_version",
]

SUPPORTED_PYDANTIC_AI_MAJOR = 2


class UnsupportedPydanticAIVersion(RuntimeError):
    """The installed pydantic-ai cannot run this code."""


def check_pydantic_ai_version(version: str = _pydantic_ai_version) -> None:
    """Raise when the installed major version is not the one this code targets.

    Called at import, so a mismatched image fails at startup with the two
    numbers in the message rather than somewhere deep in a user's agent run.
    """
    major, _, rest = version.partition(".")
    del rest
    try:
        installed_major = int(major)
    except ValueError:  # pragma: no cover - non-numeric dev version
        return
    if installed_major != SUPPORTED_PYDANTIC_AI_MAJOR:
        raise UnsupportedPydanticAIVersion(
            f"pydantic-ai {version} is installed but this build targets major "
            f"{SUPPORTED_PYDANTIC_AI_MAJOR}. The image's dependencies and its "
            "application code are out of step — rebuild it from a lockfile that "
            "matches this commit."
        )


check_pydantic_ai_version()
