"""Workspace callback URLs never receive inferred topology rewrites.

Local launchers supply explicit WORKSPACE_CALLBACK_* values. The backend must
leave ordinary API, auth, and frontend URLs untouched when those are absent.
"""

import pytest

from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

_rewrite = WorkspaceSandboxService.resolve_workspace_host_url_for_runtime


@pytest.mark.parametrize(
    "url",
    [
        "http://localhost:8710",
        "http://127.0.0.1:8710",
        "http://0.0.0.0:8710",
        "http://api.lemma.localhost:8710",
        "http://deep.api.lemma.localhost:8710",
        "http://127-0-0-1.sslip.io:8710",
        "https://api.lemma.work",
    ],
)
def test_all_hosts_are_left_untouched(url):
    assert _rewrite("docker", url) == url
    assert _rewrite("agentbox", url) == url
