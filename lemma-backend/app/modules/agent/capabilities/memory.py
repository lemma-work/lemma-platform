"""Memory capability: the memory contract as a standing system-prompt fragment.

A pure-instructions capability, like ``SurfacePlatformCapability`` and unlike
every other toolset-backed one — because ``AgentToolset.MEMORY`` carries no
tools. Memory is pod files, so the reading and writing already belong to
WORKSPACE_CLI and POD; what MEMORY adds is knowing the convention and having
the four ``AGENTS.md`` scopes loaded into the runtime brief.

That is also why this cannot go through ``InstructedToolsetCapability`` like
the other fragments: there is no toolset object for the assembler to match on.
It is appended from ``ctx.memory_enabled`` instead, which the run-context
builder resolves once so this and the brief builder always agree.

The fragment is the same file the remote harness reads through
``FRAGMENT_BY_TOOLSET`` — stable per run, so it belongs in the cached prefix.
"""

from __future__ import annotations

from pydantic_ai.capabilities import AbstractCapability

from app.modules.agent.domain.prompts import load_memory_prompt


class MemoryCapability(AbstractCapability[object]):
    """Append the memory contract to the cached system-prompt prefix."""

    def get_serialization_name(self) -> str | None:  # pragma: no cover - metadata
        return "memory"

    def get_instructions(self) -> str:
        return load_memory_prompt()
