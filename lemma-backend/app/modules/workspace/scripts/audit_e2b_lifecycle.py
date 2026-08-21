"""What every E2B sandbox in this account does when its timeout elapses.

Two uses. First, verification: `E2BSandboxProvider.create` now asks E2B to pause
rather than kill, and this is how you confirm that newly created sandboxes
actually carry it -- a lifecycle is chosen at create and **E2B offers no way to
change it afterwards**, so a mistake here is permanent for every sandbox made
while it was wrong.

Second, inventory. Sandboxes created before that shipped keep the SDK default,
`on_timeout: "kill"`, and on this provider the sandbox is the disk. They are
also already stale by template, so the next ensure replaces them; this reports
how many there are before that happens.

Run:

    uv run python -m app.modules.workspace.scripts.audit_e2b_lifecycle
"""

from __future__ import annotations

import asyncio
import collections
import sys
from typing import Any

from app.core.config import reveal_secret
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.providers.e2b_common import meta_sandbox_id, meta_template


async def _iterate(paginator) -> list[Any]:
    """Every page, not just the first.

    Reading one page is a mistake this module has made before. It looks like it
    worked and silently skips most of the fleet, which for an audit means
    reporting a reassuring number that is not true.
    """
    found: list[Any] = []
    while paginator.has_next:
        found.extend(await paginator.next_items())
    return found


def _on_timeout(info) -> str:
    lifecycle = getattr(info, "lifecycle", None) or {}
    action = lifecycle.get("on_timeout") if hasattr(lifecycle, "get") else None
    # Absent means the sandbox predates the field, which is the same exposure as
    # an explicit kill -- so it must not read as "fine".
    return str(action or "kill (unset)")


async def main() -> int:
    from e2b import AsyncSandbox
    from e2b.sandbox.sandbox_api import SandboxQuery, SandboxState

    api_key = reveal_secret(workspace_settings.e2b_api_key)
    if not api_key:
        print("E2B_API_KEY is not set", file=sys.stderr)
        return 2
    namespace = workspace_settings.e2b_metadata_namespace
    owned_by_us = meta_sandbox_id(namespace)
    template_key = meta_template(namespace)

    sandboxes: list[Any] = []
    for state in (SandboxState.RUNNING, SandboxState.PAUSED):
        sandboxes.extend(
            await _iterate(
                AsyncSandbox.list(api_key=api_key, query=SandboxQuery(state=[state]))
            )
        )

    # Namespace, not "everything in the account" -- these credentials are shared,
    # and a report that counts a stranger's sandboxes is worse than no report.
    ours = [s for s in sandboxes if (s.metadata or {}).get(owned_by_us)]
    by_action = collections.Counter(_on_timeout(s) for s in ours)
    unstamped = [s for s in ours if not (s.metadata or {}).get(template_key)]

    print(f"visible in account: {len(sandboxes)}")
    print(f"carrying the {namespace!r} namespace: {len(ours)}")
    print("\non timeout:")
    for action, count in sorted(by_action.items(), key=lambda kv: -kv[1]):
        print(f"  {count:>5}  {action}")
    print(f"\nno recorded template (replaced on next ensure): {len(unstamped)}")
    legacy = sum(count for action, count in by_action.items() if "pause" not in action)
    if legacy:
        print(
            f"\n{legacy} sandbox(es) predate the pause-on-timeout lifecycle and "
            "cannot be changed in place. They are replaced on their next ensure."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
