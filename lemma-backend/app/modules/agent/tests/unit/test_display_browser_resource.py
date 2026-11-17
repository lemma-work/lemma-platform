"""`display_resource(type=BROWSER)` shows the browser on the run's machine.

It called `create_browser_access(ctx.user_id, pod_id=..., actor_id=...)` --
the person's id where a machine's goes, and two arguments the method does not
take -- and the broad `except` around it turned the TypeError into the error
every BROWSER display returned. The doubles here have the real method's
signature, so that call fails them the way it failed in production.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from uuid import UUID, uuid4


from app.modules.agent.domain.context import ToolContext
from app.modules.agent.infrastructure.tools.user_interaction.models import (
    DisplayResourceRequest,
    DisplayResourceType,
)
from app.modules.agent.infrastructure.tools.user_interaction.tools import (
    display_resource,
)

MACHINE = uuid4()
EXPIRES = datetime.now(timezone.utc) + timedelta(minutes=30)


async def test_a_browser_display_opens_the_runs_machine(monkeypatch) -> None:
    asked: dict[str, object] = {}

    async def resolve_run_machine(ctx, machine):
        asked["ref"] = machine
        return SimpleNamespace(machine_id=MACHINE)

    async def create_browser_access(
        self, user_id: UUID, *, ttl_seconds: int, ensure_sandbox: bool = True
    ):
        asked["machine"] = user_id
        asked["ttl"] = ttl_seconds
        return SimpleNamespace(url="https://browser.test/view", expires_at=EXPIRES)

    monkeypatch.setattr(
        "app.modules.agent.infrastructure.tools.workspace_cli.run_machine."
        "resolve_run_machine",
        resolve_run_machine,
    )
    monkeypatch.setattr(
        "app.modules.workspace.services.workspace_sandbox_service."
        "WorkspaceSandboxService.create_browser_access",
        create_browser_access,
    )
    ctx = ToolContext(user_id=uuid4(), pod_id=uuid4(), conversation_id=uuid4())

    response = await display_resource(
        ctx, DisplayResourceRequest(type=DisplayResourceType.BROWSER)
    )

    assert response.success is True, response.error
    assert response.url == "https://browser.test/view"
    assert asked == {"ref": None, "machine": MACHINE, "ttl": 1800}
