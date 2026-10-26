"""The pod file tools, held to memory, for the pass that keeps it after a turn.

The pass acts as the agent did in the run, so it calls the agent's own tools:
the same authorization, the same memory-brief invalidation on write, the same
warning when an index grows past what the brief can show. What it does not get
is their reach. The agent wrote anywhere its grants allowed; the pass reads
only memory and writes only the memory ``## Your Memory`` gives this agent --
the two indexes everyone shares, its own two folders, and the pod-shared topic
files under ``/memory`` that are not another agent's.

A path outside that is answered with a refusal the model can read, never
raised: a pass that reaches for the wrong file should write the right one, not
die.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic_ai.tools import RunContext
from pydantic_ai.toolsets import FunctionToolset

from app.modules.agent.domain.agent_memory_paths import AgentMemoryPaths
from app.modules.agent.domain.value_objects import JsonObject
from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.pod import pod_file_tools
from app.modules.agent.infrastructure.tools.pod.models import (
    PodReadFileRequest,
    PodWriteFileRequest,
    SearchFilesRequest,
)
from app.modules.datastore.contracts import (
    DatastoreValidationError,
    normalize_datastore_path,
)

_SHARED_ROOT = "/memory"
_PERSONAL_ROOT = "/me"
_AGENT_FOLDERS = "/memory/agents"


def _is_under(path: str, root: str) -> bool:
    return path == root or path.startswith(f"{root}/")


@dataclass(frozen=True, slots=True)
class MemoryScope:
    """Which paths one agent's memory pass may read and write."""

    paths: AgentMemoryPaths

    def refusal(self, path: str | None, *, write: bool) -> JsonObject | None:
        """``None`` when the pass may touch ``path``, else the result saying why not."""
        if not path or not path.startswith("/"):
            return _refused(path, "Give an absolute path under /memory or /me.")
        try:
            normalized = normalize_datastore_path(path)
        except DatastoreValidationError:
            return _refused(path, "That is not a valid path.")
        allowed = self.may_write(normalized) if write else self.may_read(normalized)
        if allowed:
            return None
        if write:
            return _refused(
                path,
                "Only memory files may be written here: the two shared indexes, "
                f"`{self.paths.pod_agent_folder}/`, `{self.paths.personal_agent_folder}/`, "
                "or a pod-shared topic file under `/memory` that is not another "
                "agent's.",
            )
        return _refused(path, "Only files under /memory or /me may be read here.")

    def may_read(self, path: str) -> bool:
        return _is_under(path, _SHARED_ROOT) or _is_under(path, _PERSONAL_ROOT)

    def may_write(self, path: str) -> bool:
        paths = self.paths
        if path in (paths.pod_index, paths.personal_index):
            return True
        if _is_under(path, paths.pod_agent_folder) or _is_under(
            path, paths.personal_agent_folder
        ):
            return True
        return _is_under(path, _SHARED_ROOT) and not _is_under(path, _AGENT_FOLDERS)


def _refused(path: str | None, reason: str) -> JsonObject:
    return {"success": False, "path": path, "error": reason}


def memory_pass_toolset(scope: MemoryScope) -> FunctionToolset[ToolContext]:
    """``pod_search_files``, ``pod_read_file`` and ``pod_write_file``, held to ``scope``.

    The names the memory contract teaches, so the rules the pass is given
    describe the tools it has.
    """

    async def pod_search_files(
        ctx: RunContext[ToolContext], request: SearchFilesRequest
    ) -> JsonObject:
        """Search memory files; ``scope_path`` must be under /memory or /me."""
        refused = scope.refusal(request.scope_path, write=False)
        if refused is not None:
            return refused
        return await pod_file_tools.pod_search_files(ctx, request)

    async def pod_read_file(
        ctx: RunContext[ToolContext], request: PodReadFileRequest
    ) -> JsonObject:
        """Read a memory file's text."""
        refused = scope.refusal(request.path, write=False)
        if refused is not None:
            return refused
        return await pod_file_tools.pod_read_file(ctx, request)

    async def pod_write_file(
        ctx: RunContext[ToolContext], request: PodWriteFileRequest
    ) -> JsonObject:
        """Write a memory file, creating or replacing it whole."""
        refused = scope.refusal(request.path, write=True)
        if refused is not None:
            return refused
        return await pod_file_tools.pod_write_file(ctx, request)

    return FunctionToolset[ToolContext](
        tools=[pod_search_files, pod_read_file, pod_write_file]
    )


__all__ = ["MemoryScope", "memory_pass_toolset"]
