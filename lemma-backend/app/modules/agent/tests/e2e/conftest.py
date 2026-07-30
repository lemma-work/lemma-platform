"""Agent module E2E fixtures."""

import pytest

from app.core.infrastructure.db.session import async_session_maker
from app.core.infrastructure.db.uow_factory import SessionUnitOfWorkFactory
from app.core.infrastructure.jobs.streaq_job_queue import (
    get_streaq_job_queue,
)
from app.modules.agent.events.handlers import reconcile_agent_approval_now
from app.modules.test_support.e2e import fixtures as e2e_fixtures
from app.modules.test_support.e2e.runtime import (
    backend_server as runtime_backend_server,
    configure_workspace_api_url as runtime_configure_workspace_api_url,
    function_image as runtime_function_image,
    local_agentbox_server as runtime_local_agentbox_server,
    workspace_image as runtime_workspace_image,
)

pytestmark = pytest.mark.e2e

test_network = e2e_fixtures.test_network
postgres_container = e2e_fixtures.postgres_container
supertokens_container = e2e_fixtures.supertokens_container
redis_container = e2e_fixtures.redis_container
test_database_url = e2e_fixtures.test_database_url
test_redis_url = e2e_fixtures.test_redis_url
e2e_settings = e2e_fixtures.e2e_settings
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
local_agentbox_server = runtime_local_agentbox_server
function_image = runtime_function_image
workspace_image = runtime_workspace_image


@pytest.fixture(autouse=True)
def execute_approval_jobs_inline(test_app, request):
    """Keep approval-flow E2Es deterministic without a separate worker process.

    Production always queues this job. These tests already validate tool/resume
    semantics in-process (including monkeypatches), so only the new approval job
    is executed inline; every other queue operation still uses the real adapter.
    """
    if request.node.get_closest_marker("approval_worker"):
        yield
        return

    real_queue = get_streaq_job_queue()

    class _ApprovalAwareQueue:
        async def enqueue(self, job_name: str, **kwargs):
            if job_name == "reconcile_agent_approval":
                await reconcile_agent_approval_now(
                    kwargs["context"],
                    uow_factory=SessionUnitOfWorkFactory(async_session_maker),
                )
                return object()
            return await real_queue.enqueue(job_name, **kwargs)

    test_app.dependency_overrides[get_streaq_job_queue] = _ApprovalAwareQueue
    yield
    test_app.dependency_overrides.pop(get_streaq_job_queue, None)
