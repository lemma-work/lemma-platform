"""Which tool surface the pod's own assistant gets: the toolset experiment's arms.

Temporary scaffolding (toolset experiment, plan A7/B5): the arms are compared
live, and the winner becomes the pod-default ``RunPolicy`` row, after which
this switch goes.

- ``today``: every tool, the optional groups behind tool search.
- ``direct``: every tool in view, nothing behind search.
- ``core_cli``: only the core tools (``ToolPolicy.core``); everything else is
  the ``lemma`` CLI in the sandbox.
- ``core_native``: the core tools in view, the rest behind the provider's own
  tool search where it has one (the tool array never changes), and left to
  the CLI where it does not.
"""

from __future__ import annotations

from enum import Enum

from app.modules.agent.domain.run_kind import RunKind


class ToolProfile(str, Enum):
    TODAY = "today"
    DIRECT = "direct"
    CORE_CLI = "core_cli"
    CORE_NATIVE = "core_native"


def run_tool_profile(
    kind: RunKind, *, pod_default: bool, deployed: ToolProfile
) -> ToolProfile:
    """The profile a run gets: the deployment's, where its kind follows it.

    Only the pod's own assistant: a named agent chose its tools.
    """
    if not pod_default or not kind.policy.follows_tool_profile:
        return ToolProfile.TODAY
    return deployed


__all__ = ["ToolProfile", "run_tool_profile"]
