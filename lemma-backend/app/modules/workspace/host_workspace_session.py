"""A workspace session whose paths are the owner's own, on their Mac.

The VM session canonicalises every path under ``/home/user`` because that is
the only tree a VM sandbox has. A host sandbox's root is a real folder on the
Mac (docs/architecture/desktop-host-execution.md §5), so this session keeps the
host's absolute paths as they are and resolves relative ones against the root.
It never rewrites a command string, and it does not police containment: the
exec-server checks every file path against the root, ``$TMPDIR`` and the
owner's grants, and Seatbelt enforces the same underneath it.

Everything else -- output cursors, backpressure, process collection -- is the
VM session's, unchanged, because the provider below speaks the same protocol.
"""

from __future__ import annotations

import posixpath

from sandbox_runtime.paths import WORKSPACE_ROOT

from app.modules.workspace.sandbox_session import SandboxWorkspaceSession


def host_path(path: str, *, base: str) -> str:
    """An absolute host path: as given, or relative to ``base``."""
    if not path:
        raise ValueError("workspace path must not be empty")
    return posixpath.normpath(
        path if path.startswith("/") else posixpath.join(base, path)
    )


class HostWorkspaceSession(SandboxWorkspaceSession):
    """``SandboxWorkspaceSession`` rooted at a folder on the user's Mac."""

    def __init__(self, *, root: str, **kwargs: object) -> None:
        if not root.startswith("/"):
            raise ValueError("a host workspace root must be absolute")
        super().__init__(initial_cwd=WORKSPACE_ROOT, **kwargs)
        self.root = posixpath.normpath(root)
        self._cwd = self.root

    async def exec_command(
        self,
        *,
        cmd: str,
        max_output_tokens: int | None = None,
        tty: bool = False,
        workdir: str | None = None,
        yield_time_ms: int | None = None,
        timeout: int | None = 300,
        cols: int = 120,
        rows: int = 40,
    ) -> dict[str, object]:
        """The VM session's, with ``workdir`` resolved against the host root."""
        return await super().exec_command(
            cmd=cmd,
            max_output_tokens=max_output_tokens,
            tty=tty,
            workdir=host_path(workdir, base=self._cwd) if workdir else None,
            yield_time_ms=yield_time_ms,
            timeout=timeout,
            cols=cols,
            rows=rows,
        )

    async def _resolve_path(self, path: str) -> str:
        return host_path(path, base=self._cwd)
