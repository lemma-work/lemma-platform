"""Workspace callback host URL rewriting for container runtimes.

A function/agent running inside a Docker AgentBox container reaches the host
backend via ``host.docker.internal``. The resolver must rewrite every host that
actually points at the host loopback — including reserved ``*.localhost`` names
and the legacy ``sslip.io`` alias — while leaving real public hosts untouched.
"""

import pytest

from app.modules.workspace.services.workspace_sandbox_service import (
    WorkspaceSandboxService,
)

_rewrite = WorkspaceSandboxService.resolve_workspace_host_url_for_runtime


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("http://localhost:8710", "http://host.docker.internal:8710"),
        ("http://127.0.0.1:8710", "http://host.docker.internal:8710"),
        ("http://0.0.0.0:8710", "http://host.docker.internal:8710"),
        ("http://api.lemma.localhost:8710", "http://host.docker.internal:8710"),
        ("http://deep.api.lemma.localhost:8710", "http://host.docker.internal:8710"),
        # Legacy sslip.io dashed-IP loopback alias.
        ("http://127-0-0-1.sslip.io:8710", "http://host.docker.internal:8710"),
        ("https://127-0-0-1.sslip.io", "https://host.docker.internal"),
    ],
)
def test_loopback_hosts_rewritten_for_docker(url, expected):
    assert _rewrite("docker", url) == expected
    assert _rewrite("agentbox", url) == expected


@pytest.mark.parametrize(
    "url",
    [
        "https://api.lemma.work",
        "https://api.lemma.example.com:8710",
        # A non-loopback sslip.io host must NOT be rewritten.
        "http://10-0-0-5.sslip.io:8710",
    ],
)
def test_public_hosts_left_untouched(url):
    assert _rewrite("docker", url) == url


def test_non_container_runtime_is_passthrough():
    url = "http://127-0-0-1.sslip.io:8710"
    assert _rewrite("kubernetes", url) == url
    assert _rewrite("local", url) == url
