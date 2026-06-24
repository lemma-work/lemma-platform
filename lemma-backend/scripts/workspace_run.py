#!/usr/bin/env python3
"""workspace_run.py — Run commands in a user's workspace sandbox.

Resolves the user by email, ensures their workspace sandbox is running, and
executes commands via the workspace session API.

Usage:
    # Run an arbitrary shell command
    uv run scripts/workspace_run.py --user lemma@lemma.work --command "ls /workspace"

    # Start a long-running process in the sandbox
    uv run scripts/workspace_run.py --user lemma@lemma.work --command "python -m http.server 8888"
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from uuid import UUID

# Make the project root importable when running as a script
sys.path.insert(0, str(Path(__file__).parent.parent))

from sqlalchemy import select

from app.core.infrastructure.db.session import async_session_maker
from app.modules.identity.infrastructure.models import User
from app.modules.identity.infrastructure.supertokens_auth.initialization import (
    initialize_supertokens,
)
from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)
from app.core.log.log import setup_logging, get_logger

logger = get_logger(__name__)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _resolve_user_id(email: str) -> UUID:
    """Look up a user UUID by email address."""
    async with async_session_maker() as session:
        result = await session.execute(select(User).where(User.email == email))
        user = result.scalars().first()
    if user is None:
        print(f"ERROR: no user found with email '{email}'", file=sys.stderr)
        sys.exit(1)
    return user.id


def _print_info(label: str, value: str) -> None:
    print(f"  {label:<20} {value}")


async def _wait_for_http(session, url: str, timeout: int = 20) -> bool:
    """Poll url inside the container until HTTP 200 or timeout."""
    cmd = f"curl -s -o /dev/null -w '%{{http_code}}' {url}"
    for _ in range(timeout):
        r = await session.execute_terminal_command(cmd, timeout=5)
        if (r.stdout or "").strip() == "200":
            return True
        await asyncio.sleep(1)
    return False


# ---------------------------------------------------------------------------
# Commands
# ---------------------------------------------------------------------------


async def cmd_run(user_email: str, command: str, timeout: int) -> int:
    """Run a shell command inside the user's workspace and stream output."""
    user_id = await _resolve_user_id(user_email)

    print(f"\n[workspace] Resolving sandbox for {user_email} ...")
    service = WorkspaceSandboxService()
    sandbox_info = await service.get_or_create_sandbox(user_id)

    _print_info("user_id:", str(user_id))
    _print_info("workspace_url:", sandbox_info["workspace_url"])
    _print_info("status:", sandbox_info.get("status", "?"))
    print()

    session = await service.get_session(user_id, pod_id=None)
    async with session:
        print(f"$ {command}\n{'─' * 60}")
        result = await session.execute_terminal_command(command, timeout=timeout)
        if result.stdout:
            print(result.stdout, end="")
        if result.stderr:
            print(result.stderr, end="", file=sys.stderr)
        print("─" * 60)
        exit_code = result.exit_code if result.exit_code is not None else (0 if result.success else 1)
        print(f"exit code: {exit_code}")
        return exit_code


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Run commands inside a user's workspace container (K8s).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--user", "-u", required=True, help="User email address")

    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--command", "-c", metavar="CMD", help="Shell command to execute")

    parser.add_argument(
        "--timeout", "-t", type=int, default=300,
        help="Command timeout in seconds (default: 300, only for --command)",
    )
    parser.add_argument("--verbose", "-v", action="store_true", help="Enable verbose logging")

    args = parser.parse_args()

    setup_logging(env="development" if args.verbose else "production")
    initialize_supertokens()

    if args.command:
        sys.exit(asyncio.run(cmd_run(args.user, args.command, args.timeout)))


if __name__ == "__main__":
    main()
