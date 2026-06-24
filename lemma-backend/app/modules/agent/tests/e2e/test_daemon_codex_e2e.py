from __future__ import annotations

import pytest

from app.modules.agent.tests.e2e.daemon_harness_e2e_helpers import (
    run_real_daemon_harness_flow,
)

pytestmark = [pytest.mark.e2e, pytest.mark.slow, pytest.mark.local_cli, pytest.mark.provider]


@pytest.mark.asyncio
@pytest.mark.parametrize("start_mode", ["foreground", "background"])
async def test_real_codex_daemon_harness_full_flow(
    start_mode,
    authenticated_client,
    fixed_test_org,
    fixed_test_user,
    backend_server,
    configure_workspace_api_url,
    tmp_path,
    worker,
):
    del configure_workspace_api_url
    await run_real_daemon_harness_flow(
        harness_kind="CODEX",
        authenticated_client=authenticated_client,
        fixed_test_org=fixed_test_org,
        fixed_test_user=fixed_test_user,
        backend_server=backend_server,
        tmp_path=tmp_path,
        worker=worker,
        background=(start_mode == "background"),
    )
