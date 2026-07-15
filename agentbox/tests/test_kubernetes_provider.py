from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from kubernetes.client.rest import ApiException  # noqa: E402

from agentbox.kubernetes import SandboxKubernetesClient  # noqa: E402
from agentbox.schemas import SandboxEnsureRequest  # noqa: E402


def _pod(phase: str, ready: bool) -> SimpleNamespace:
    container = SimpleNamespace(name="sandbox", ready=ready)
    status = SimpleNamespace(
        phase=phase,
        pod_ip="10.0.0.1" if ready else None,
        container_statuses=[container],
    )
    return SimpleNamespace(status=status)


class _FakeCoreV1:
    """Stateful stand-in for CoreV1Api driven through run_sync."""

    def __init__(self, pod: SimpleNamespace | None) -> None:
        self.pod = pod
        self.created = 0
        self.deleted = 0

    def read_namespaced_pod(self, name: str, namespace: str):  # noqa: ANN001
        del name, namespace
        if self.pod is None:
            raise ApiException(status=404)
        return self.pod

    def create_namespaced_pod(self, namespace: str, body):  # noqa: ANN001
        del namespace, body
        self.created += 1
        self.pod = _pod("Running", ready=True)
        return self.pod

    def delete_namespaced_pod(
        self, name: str, namespace: str, grace_period_seconds: int
    ):  # noqa: ANN001
        del name, namespace, grace_period_seconds
        self.deleted += 1
        self.pod = None
        return SimpleNamespace()


def _client(core_v1: _FakeCoreV1) -> SandboxKubernetesClient:
    client = SandboxKubernetesClient.__new__(SandboxKubernetesClient)
    client.core_v1 = core_v1  # type: ignore[attr-defined]
    return client


def test_create_recreates_terminal_pod() -> None:
    # A Failed pod (restart_policy=Never) can never become ready, so create must
    # delete it and provision a fresh pod rather than waiting on the dead one.
    core = _FakeCoreV1(_pod("Failed", ready=False))
    client = _client(core)

    status = asyncio.run(client.create("sandbox1", SandboxEnsureRequest()))

    assert core.deleted == 1
    assert core.created == 1
    assert status.ready is True
    assert status.status == "RUNNING"


def test_create_recreates_stopped_pod() -> None:
    # A Succeeded ("STOPPED") pod is equally terminal and must be recreated.
    core = _FakeCoreV1(_pod("Succeeded", ready=False))
    client = _client(core)

    status = asyncio.run(client.create("sandbox1", SandboxEnsureRequest()))

    assert core.deleted == 1
    assert core.created == 1
    assert status.ready is True


def test_create_returns_ready_pod_without_recreating() -> None:
    core = _FakeCoreV1(_pod("Running", ready=True))
    client = _client(core)

    status = asyncio.run(client.create("sandbox1", SandboxEnsureRequest()))

    assert core.deleted == 0
    assert core.created == 0
    assert status.ready is True


def test_create_provisions_when_pod_missing() -> None:
    core = _FakeCoreV1(None)
    client = _client(core)

    status = asyncio.run(client.create("sandbox1", SandboxEnsureRequest()))

    assert core.deleted == 0
    assert core.created == 1
    assert status.ready is True


def test_release_deletes_compute_for_recreation() -> None:
    core = _FakeCoreV1(_pod("Running", ready=True))
    client = _client(core)

    assert asyncio.run(client.release("sandbox1")) is True
    assert core.deleted == 1
    assert core.pod is None
