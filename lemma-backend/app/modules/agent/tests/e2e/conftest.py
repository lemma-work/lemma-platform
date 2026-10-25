"""Agent module E2E fixtures."""

import pytest

from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.runtime import (
    backend_server as runtime_backend_server,
    configure_workspace_api_url as runtime_configure_workspace_api_url,
    function_image as runtime_function_image,
    local_sandbox_server as runtime_local_sandbox_server,
    workspace_image as runtime_workspace_image,
)

pytestmark = pytest.mark.e2e

postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
sandbox_reachable_backend = e2e_fixtures.sandbox_reachable_backend
worker = e2e_fixtures.worker
db_manager = e2e_fixtures.db_manager
test_app = e2e_fixtures.test_app
db_session = e2e_fixtures.db_session
async_client = e2e_fixtures.async_client
e2e_process_clients = e2e_fixtures.e2e_process_clients
fixed_test_user = e2e_fixtures.fixed_test_user
authenticated_client = e2e_fixtures.authenticated_client
fixed_test_org = e2e_fixtures.fixed_test_org
scenario = e2e_fixtures.scenario
backend_server = runtime_backend_server
configure_workspace_api_url = runtime_configure_workspace_api_url
local_sandbox_server = runtime_local_sandbox_server
function_image = runtime_function_image
workspace_image = runtime_workspace_image


@pytest.fixture(autouse=True)
def execute_approval_jobs_inline(monkeypatch, request):
    """Keep approval-flow E2Es deterministic without waiting on a worker.

    Production writes an ``agent.approval.execute`` item in the decision's
    transaction and a worker runs it. These tests validate the tool and resume
    semantics in-process (including their monkeypatches), which a separate
    worker process would not see, so the item is also run here, through the
    runtime's own claim and execute, once the decision commits. Mark a test
    ``approval_worker`` to leave it to the real worker.
    """
    if request.node.get_closest_marker("approval_worker"):
        return

    from app.modules.agent.application.pauses import resolve
    from app.modules.test_support.e2e.agent_work import run_approval_work

    staged = resolve.stage_approval_execution

    async def _stage_then_run_after_commit(uow, **kwargs) -> None:
        await staged(uow, **kwargs)

        async def _run() -> None:
            await run_approval_work()

        uow.after_commit(_run)

    monkeypatch.setattr(
        resolve,
        "stage_approval_execution",
        _stage_then_run_after_commit,
    )
