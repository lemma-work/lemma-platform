"""The parts of connected machines that need no socket: codes, names, paths,
addresses, the replica proxy's signature, and who may call the routes."""

from __future__ import annotations

import time
from uuid import uuid4

import pytest

from app.core.api.actor_policy import app_may_call, workload_may_call
from app.modules.workspace.config import workspace_settings
from app.modules.workspace.domain.machine_errors import MachineNotConnected
from app.modules.workspace.domain.machine_connection import (
    CODE_LENGTH,
    MachineConnection,
    default_name,
    generate_pairing_code,
    normalize_pairing_code,
)
from app.modules.workspace.providers.connected import (
    ConnectedMachineProvider,
    runtime_token,
)
from app.modules.workspace.providers.docker import RuntimeCredentialSigner
from app.modules.workspace.services.machine_link import network, routes

pytestmark = pytest.mark.unit

_KEY = "unit-runtime-credential-key-32-bytes!!"


def _provider(home: str | None) -> ConnectedMachineProvider:
    return ConnectedMachineProvider(
        MachineConnection(
            id=uuid4(),
            user_id=uuid4(),
            name="laptop",
            display_name="Laptop",
            mode="host",
            pod_id=None,
            platform="macos",
            home=home,
        ),
        RuntimeCredentialSigner(key=_KEY.encode()),
    )


def test_a_code_is_read_back_however_a_person_typed_it() -> None:
    code = generate_pairing_code()
    assert len(code.replace("-", "")) == CODE_LENGTH
    assert normalize_pairing_code(code.lower().replace("-", " ")) == code
    assert normalize_pairing_code("abcd efgh jkmo") == "ABCD-EFGH-JKM0"
    assert normalize_pairing_code("ILO1-2345-6789") == "1101-2345-6789"


def test_a_default_name_is_a_ref_safe_slug() -> None:
    assert default_name("Anukul's MacBook Pro") == "anukul-s-macbook-pro"
    assert default_name("!!!") == "machine"


def test_lemma_paths_move_under_a_host_mode_home_and_back() -> None:
    provider = _provider("/Users/me/lemma-machine")
    assert provider.to_machine("/home/user") == "/Users/me/lemma-machine"
    assert (
        provider.to_machine("/home/user/lemma/c/x.txt")
        == "/Users/me/lemma-machine/lemma/c/x.txt"
    )
    # Not under the home: left alone, not mangled.
    assert provider.to_machine("/home/username/x") == "/home/username/x"
    assert provider.to_machine("/tmp/x") == "/tmp/x"
    assert (
        provider.from_machine("/Users/me/lemma-machine/lemma/x") == "/home/user/lemma/x"
    )
    assert provider.from_machine("/Users/me/lemma-machine-2/x") == (
        "/Users/me/lemma-machine-2/x"
    )


def test_an_image_mode_home_is_the_sandbox_home_and_nothing_moves() -> None:
    for provider in (_provider("/home/user"), _provider(None)):
        assert provider.to_machine("/home/user/lemma") == "/home/user/lemma"
        assert provider.from_machine("/home/user/lemma") == "/home/user/lemma"


def test_not_connected_is_a_refusal_with_the_machines_name() -> None:
    error = MachineNotConnected("laptop")
    assert str(error) == "laptop is not connected."
    # Definitive, and said: a conflict, not a reason to wait or to retry.
    assert error.status_code == 409


def test_a_machine_address_names_its_connection_and_nothing_else_does() -> None:
    connection_id = uuid4()
    host = network.machine_host(connection_id)
    assert network.connection_for_host(host) == connection_id
    assert network.machine_url(connection_id, 8080).endswith(f"{host}:8080")
    for other in (
        None,
        "example.com",
        "zz.machine.lemma.internal",
        "machine.lemma.internal",
    ):
        assert network.connection_for_host(other) is None


def test_the_replica_proxy_takes_only_a_fresh_signature_for_its_connection(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        workspace_settings,
        "runtime_credential_key",
        "unit-runtime-credential-key-32-bytes!!",
    )
    connection_id = uuid4()
    headers = routes.signed_headers(connection_id)
    signature = headers[routes.SIGNATURE_HEADER]
    stamp = headers[routes.TIMESTAMP_HEADER]
    assert routes.verify_signature(connection_id, signature, stamp)
    assert not routes.verify_signature(uuid4(), signature, stamp)
    assert not routes.verify_signature(connection_id, signature, str(int(stamp) + 1))
    old = int(time.time()) - routes.SIGNATURE_WINDOW_SECONDS - 5
    assert not routes.verify_signature(
        connection_id, routes.internal_signature(connection_id, old), str(old)
    )
    assert not routes.verify_signature(connection_id, None, stamp)
    monkeypatch.setattr(workspace_settings, "runtime_credential_key", None)
    assert not routes.verify_signature(connection_id, signature, stamp)


def test_the_replica_address_is_configured_or_the_pods_own(monkeypatch) -> None:
    monkeypatch.setattr(
        workspace_settings, "machine_link_replica_url", "http://10.0.0.9:8000/"
    )
    assert routes.replica_url() == "http://10.0.0.9:8000"
    monkeypatch.setattr(workspace_settings, "machine_link_replica_url", None)
    monkeypatch.setenv("POD_IP", "10.1.2.3")
    assert routes.replica_url() == "http://10.1.2.3:8000"


def test_a_machines_runtime_token_is_its_own() -> None:
    signer = RuntimeCredentialSigner(key=_KEY.encode())
    first, second = uuid4(), uuid4()
    assert runtime_token(signer, first) == runtime_token(signer, first)
    assert runtime_token(signer, first) != runtime_token(signer, second)
    # Never the token a Docker sandbox of the same id would be given.
    assert runtime_token(signer, first) != signer.token(str(first))


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/machine-connections"),
        ("GET", "/machine-connections/pairings/{code}"),
        ("POST", "/machine-connections:pair"),
        ("DELETE", "/machine-connections/{connection_id}"),
    ],
)
def test_joining_or_removing_a_machine_is_a_persons_own_act(
    method: str, path: str
) -> None:
    """A machine runs commands as its person: no agent, function or pod app
    acting for them may approve one or take one away (main's route policy
    closes routes outside a pod to both by default; this pins that)."""
    assert not workload_may_call(method, path)
    assert not app_may_call(method, path)


def test_a_connected_machine_id_is_told_apart_by_its_tag_alone() -> None:
    from uuid import uuid7

    from app.modules.workspace.domain.host_execution import host_sandbox_id
    from app.modules.workspace.domain.machine_connection import (
        connected_machine_id,
        is_connected_machine_id,
    )

    minted = connected_machine_id()
    assert minted.version == 8
    assert is_connected_machine_id(minted)
    assert minted != connected_machine_id()
    assert not is_connected_machine_id(uuid7())
    assert not is_connected_machine_id(host_sandbox_id(uuid4()))


def test_the_router_sends_a_connected_machine_to_its_fabric_and_nothing_else() -> None:
    from app.modules.workspace.domain.host_execution import host_sandbox_id
    from app.modules.workspace.domain.machine_connection import connected_machine_id
    from app.modules.workspace.domain.sandbox import SandboxKind
    from app.modules.workspace.providers.host_routing import HostRoutingProvider

    from types import SimpleNamespace

    default, host, connected = (
        SimpleNamespace(name="docker"),
        SimpleNamespace(name="agent_host"),
        SimpleNamespace(name="connected"),
    )
    router = HostRoutingProvider(default, host, connected=connected)
    machine = connected_machine_id()
    assert router.for_sandbox(machine) is connected
    assert router.for_name(network_name(machine, SandboxKind.WORKSPACE)) is connected
    assert router.for_sandbox(host_sandbox_id(uuid4())) is host
    assert router.for_sandbox(uuid4()) is default
    # Off where connected machines are not on: a tagged id is just an id.
    assert HostRoutingProvider(default).for_sandbox(machine) is default


def network_name(sandbox_id, kind) -> str:
    from app.modules.workspace.providers import naming

    return naming.container_name(sandbox_id, kind, 1)
