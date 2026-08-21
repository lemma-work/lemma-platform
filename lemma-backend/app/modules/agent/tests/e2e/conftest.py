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

    Production always queues this job. These tests already validate the tool and
    resume semantics in-process (including their monkeypatches), which a separate
    worker process would not see, so only the approval job runs inline; every
    other queue operation still uses the real adapter. Mark a test
    ``approval_worker`` to opt back into the real queue.
    """
    if request.node.get_closest_marker("approval_worker"):
        return

    from app.core.infrastructure.db.session import async_session_maker
    from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
    from app.modules.agent.events.handlers import reconcile_agent_approval_now
    from app.modules.agent.services import approval_reconciliation

    async def _inline(*, conversation_id, approval_id, user_id, pod_id) -> None:
        await reconcile_agent_approval_now(
            {
                "conversation_id": str(conversation_id),
                "approval_id": approval_id,
                "user_id": str(user_id),
                "pod_id": str(pod_id),
            },
            uow_factory=SessionUnitOfWorkFactory(async_session_maker),
        )

    monkeypatch.setattr(
        approval_reconciliation, "queue_approval_reconciliation", _inline
    )
    monkeypatch.setattr(
        "app.modules.agent.services.conversation_service.queue_approval_reconciliation",
        _inline,
    )
