"""Every provider answers the same contract, checked against every provider.

The three providers are constructed very differently -- one wraps a Docker
engine, one an E2B SDK class, one a guest bridge subprocess -- so this is one
module that builds all three rather than a suite each inherits.

What it is for: `browser_cdp_endpoint` was added to the ops protocol,
implemented on Docker only, and shipped. E2B and the desktop guest raised
`AttributeError` from inside a request handler. Every assertion below would have
failed on that commit.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
import inspect
from pathlib import Path
from uuid import uuid4

import pytest

from app.modules.workspace.domain.sandbox import SandboxKind
from app.modules.workspace.providers.base import (
    ProviderCapability,
    ProviderCreateSpec,
    require_capability,
)
from app.modules.workspace.providers.docker import (
    DockerProviderConfig,
    DockerSandboxProvider,
    RuntimeCredentialSigner,
)
from app.modules.workspace.providers.e2b import E2BProviderConfig, E2BSandboxProvider
from app.modules.workspace.providers.e2b_common import DEFAULT_METADATA_NAMESPACE
from app.modules.workspace.providers.lemma_local import (
    LemmaLocalProviderConfig,
    LemmaLocalSandboxProvider,
)
from app.modules.workspace.providers import naming
from app.modules.workspace.testing.fake_docker_engine import FakeDockerEngine
from app.modules.workspace.testing.fake_e2b import FakeE2B
from app.modules.workspace.testing.provider_conformance import (
    assert_capability_is_consistent,
    assert_declares_capabilities,
    assert_implements_lifecycle_protocol,
    assert_implements_ops_protocol,
    check_reach_port_shape,
    check_secret_delivery_leaves_no_trace,
    check_unsupported_is_typed,
)

pytestmark = pytest.mark.asyncio

PINNED_IMAGE = "lemma-workspace@sha256:" + "b" * 64


def _deadline() -> datetime:
    return datetime.now(timezone.utc) + timedelta(seconds=30)


def _spec(sandbox_id, *, epoch: int = 1) -> ProviderCreateSpec:
    return ProviderCreateSpec(
        sandbox_id=sandbox_id,
        kind=SandboxKind.WORKSPACE,
        epoch=epoch,
        name=naming.container_name(sandbox_id, SandboxKind.WORKSPACE, epoch),
        image=PINNED_IMAGE,
        profile_name="workspace-python-v1",
        profile_digest="sha256:" + "a" * 64,
        deadline_at=_deadline(),
    )


# ---------------------------------------------------------------------------
# The three providers, each with whatever fabric double it needs
# ---------------------------------------------------------------------------


@pytest.fixture
def docker_provider() -> DockerSandboxProvider:
    return DockerSandboxProvider(
        FakeDockerEngine(),  # type: ignore[arg-type]
        DockerProviderConfig(),
        RuntimeCredentialSigner(key=b"k" * 32),
    )


@pytest.fixture
def e2b_world() -> FakeE2B:
    return FakeE2B()


@pytest.fixture
def e2b_provider(e2b_world: FakeE2B, monkeypatch) -> E2BSandboxProvider:
    provider = E2BSandboxProvider(
        E2BProviderConfig(
            api_key="test-key",
            workspace_template="lemma-workspace",
            function_template="lemma-function",
            metadata_namespace=DEFAULT_METADATA_NAMESPACE,
        )
    )
    monkeypatch.setattr(
        type(provider), "_sdk", property(lambda self: e2b_world.sandbox_class())
    )
    return provider


@pytest.fixture
def local_provider(tmp_path: Path, monkeypatch) -> LemmaLocalSandboxProvider:
    # Reuses the recording bridge the desktop provider's own tests drive, so
    # this checks the same guest behaviour they do rather than a second fake.
    from app.modules.workspace.tests.unit.test_lemma_local_provider import (
        _RECORDING_BRIDGE,
        _bridge,
    )

    bridge = _bridge(tmp_path, _RECORDING_BRIDGE)
    monkeypatch.setenv("BRIDGE_STATE", str(tmp_path / "state.json"))
    return LemmaLocalSandboxProvider(
        LemmaLocalProviderConfig(executable=str(bridge)),
        RuntimeCredentialSigner(key=b"k" * 32),
    )


@pytest.fixture
def provider(request):
    """The provider under test, one per fabric."""
    return request.getfixturevalue(request.param)


ALL_PROVIDERS = pytest.mark.parametrize(
    "provider",
    ["docker_provider", "e2b_provider", "local_provider"],
    indirect=True,
)


# ---------------------------------------------------------------------------
# The contract, asserted against each of them
# ---------------------------------------------------------------------------


@ALL_PROVIDERS
async def test_the_ops_protocol_is_fully_implemented(provider) -> None:
    assert_implements_ops_protocol(provider)


@ALL_PROVIDERS
async def test_the_lifecycle_protocol_is_fully_implemented(provider) -> None:
    assert_implements_lifecycle_protocol(provider)


@ALL_PROVIDERS
async def test_capabilities_are_declared_and_consistent(provider) -> None:
    assert_declares_capabilities(provider)
    assert_capability_is_consistent(provider)


async def test_docker_reaches_a_port_privately(docker_provider) -> None:
    instance = await docker_provider.create(_spec(uuid4()))
    endpoint = await check_reach_port_shape(docker_provider, instance)
    assert endpoint.public is False
    assert endpoint.headers == {}


async def test_e2b_reports_an_open_host_when_there_is_no_traffic_token(
    e2b_provider, e2b_world
) -> None:
    """The arrangement every sandbox created before the flag still has."""
    instance = await e2b_provider.create(_spec(uuid4()))
    endpoint = await check_reach_port_shape(e2b_provider, instance)
    assert endpoint.public is True, (
        "an E2B host with no traffic token is on the internet; a caller about "
        "to publish a port has to be told so"
    )
    assert endpoint.headers == {}


async def test_e2b_carries_the_traffic_token_when_it_has_one(
    e2b_provider, e2b_world
) -> None:
    e2b_world.traffic_access_token = "tok-abc"
    instance = await e2b_provider.create(_spec(uuid4()))
    endpoint = await check_reach_port_shape(e2b_provider, instance)
    assert endpoint.headers == {"e2b-traffic-access-token": "tok-abc"}
    assert endpoint.public is False


async def test_the_desktop_guest_reaches_a_declared_port(local_provider) -> None:
    instance = await local_provider.create(_spec(uuid4()))
    endpoint = await check_reach_port_shape(local_provider, instance, port=4848)
    assert endpoint.url == "http://127.0.0.1:4848"
    assert endpoint.public is False


async def test_e2b_delivers_a_secret_without_putting_it_in_a_command(
    e2b_provider, e2b_world
) -> None:
    instance = await e2b_provider.create(_spec(uuid4()))
    await check_secret_delivery_leaves_no_trace(
        e2b_provider,
        instance,
        commands_run=lambda: list(e2b_world.commands),
    )
    assert (
        e2b_world.files["/tmp/lemma-conformance/token"] == b"conformance-secret-value"
    )
    assert any("chmod 600" in c for c in e2b_world.commands), (
        "the file has to be narrowed to its owner; files.write has no mode"
    )


async def test_docker_delivers_a_secret_as_an_archive(docker_provider) -> None:
    instance = await docker_provider.create(_spec(uuid4()))
    engine = docker_provider._engine
    await check_secret_delivery_leaves_no_trace(
        docker_provider,
        instance,
        # Docker delivery runs no commands at all; the archive is the mechanism.
        commands_run=list,
    )
    # Unpacked one level up, with the directory inside the archive. Docker's
    # archive API refuses a destination that does not exist, and the
    # directories credentials go into are created by the processes that read
    # them -- which have not necessarily run. Letting tar create it is what
    # makes delivery work on a container that has only just started, and
    # nothing but a real sandbox caught that.
    destinations = [path for _, path in engine.archives]
    assert "/tmp" in destinations, destinations

    import tarfile
    from io import BytesIO

    payload = next(blob for path, blob in engine.archive_payloads if path == "/tmp")
    with tarfile.open(fileobj=BytesIO(payload)) as archive:
        names = archive.getnames()
        folder = archive.getmember("lemma-conformance")
        secret = archive.getmember("lemma-conformance/token")
    assert names == ["lemma-conformance", "lemma-conformance/token"], names
    assert folder.isdir() and folder.mode == 0o700, (
        "the directory is no more readable than the secret"
    )
    assert secret.mode == 0o600, "the secret is readable only by its owner"


async def test_a_capability_a_fabric_lacks_is_refused_in_words() -> None:
    """The failure mode this whole file exists to prevent."""

    class FabricWithoutPorts:
        capabilities = frozenset()
        provider_name = "paper"

    async def call() -> object:
        require_capability(FabricWithoutPorts(), ProviderCapability.PORT_REACH)
        return None

    await check_unsupported_is_typed(call)


async def test_an_undeclared_capability_is_treated_as_absent() -> None:
    """A provider added later is refused honestly until it says otherwise."""

    class NewFabric:
        pass

    async def call() -> object:
        require_capability(NewFabric(), ProviderCapability.SECRET_DELIVERY)
        return None

    await check_unsupported_is_typed(call)


async def test_every_provider_declares_the_same_two_capabilities(
    docker_provider, e2b_provider, local_provider
) -> None:
    """Parity is the point: a surface built on these works everywhere.

    If a provider ever legitimately cannot do one of these, this test is where
    that decision gets written down -- not discovered in production.
    """
    expected = frozenset(
        {ProviderCapability.PORT_REACH, ProviderCapability.SECRET_DELIVERY}
    )
    for provider in (docker_provider, e2b_provider, local_provider):
        assert provider.capabilities == expected, type(provider).__name__


@pytest.mark.asyncio(loop_scope="function")
async def test_the_protocol_method_list_is_not_empty() -> None:
    """A protocol whose members stopped being discoverable would pass vacuously."""
    from app.modules.workspace.testing.provider_conformance import (
        LIFECYCLE_PROTOCOL_METHODS,
        OPS_PROTOCOL_METHODS,
    )

    assert "reach_port" in OPS_PROTOCOL_METHODS
    assert "deliver_secret" in OPS_PROTOCOL_METHODS
    assert "stat_file" in OPS_PROTOCOL_METHODS
    assert len(LIFECYCLE_PROTOCOL_METHODS) > 3


async def test_new_e2b_sandboxes_do_not_answer_the_internet(
    e2b_provider, e2b_world
) -> None:
    """The exposure this closes was reachable, unauthenticated, in production.

    `lemma-ensure-display.sh` republishes the agent-browser dashboard on 0.0.0.0:4848,
    which on Docker is the container's own network and on E2B is a public name.
    Nothing in front of it asks who you are. Closing public traffic at create is
    what puts E2B's own doorkeeper there; `reach_port` then carries the token.

    Unconditional now. This was `E2B_ALLOW_PUBLIC_TRAFFIC`, and a deployment
    that set it true got sandboxes whose every listening port answered the
    internet -- for no gain, because nothing outside the backend ever needs a
    sandbox's own address.
    """
    await e2b_provider.create(_spec(uuid4()))
    assert e2b_world.created_public_traffic == [False], (
        "a sandbox created without this argument is open by default"
    )


def test_no_setting_can_open_a_sandbox_to_the_internet() -> None:
    """There is no longer a position of a knob that exposes the browser.

    The pairing is the point: the constant says what is sent, and this says
    nothing can be introduced that changes it per deployment. Re-adding the
    setting fails here even if every other test still passes, because every
    other test would be exercising whatever the default happened to be.
    """
    from app.modules.workspace.config import WorkspaceSettings
    from app.modules.workspace.providers.e2b_config import CLOSED_TO_THE_INTERNET

    assert CLOSED_TO_THE_INTERNET == {"allow_public_traffic": False}
    assert not [
        name
        for name, field in WorkspaceSettings.model_fields.items()
        if "public_traffic" in name
        or "PUBLIC_TRAFFIC"
        in (
            field.validation_alias.choices[0].upper()
            if field.validation_alias
            else name.upper()
        )
    ]


async def test_the_arguments_we_create_sandboxes_with_are_ones_the_sdk_takes(
    e2b_provider, e2b_world
) -> None:
    """Bind our real call against the real library, not against the double.

    This is the test that was missing. `allow_public_traffic` was passed to
    `AsyncSandbox.create` as a keyword of its own for the life of this branch,
    and no released `e2b` has ever had such a parameter -- it is a key on
    `network`. `create` forwards its extra keywords to `ConnectionConfig`,
    which raises on a name it does not know, so every real sandbox creation
    raised `TypeError` while `test_new_e2b_sandboxes_do_not_answer_the_internet`
    passed, because the double had been written to match our call rather than
    the library.

    Skipped where the extra is absent, which is every unit lane; there, the
    double's own signature carries the guarantee. This is what notices when the
    SDK moves a parameter again.
    """
    e2b = pytest.importorskip("e2b", reason="the e2b extra is not installed")

    await e2b_provider.create(_spec(uuid4()))
    (passed,) = e2b_world.created_kwargs

    # `bind` raises exactly what production raised.
    inspect.signature(e2b.AsyncSandbox.create).bind(**passed)

    # And the flag really is a network key, which is why it has to travel there.
    from e2b.sandbox.sandbox_api import SandboxNetworkOpts

    assert "allow_public_traffic" in SandboxNetworkOpts.__annotations__
    assert passed["network"] == {"allow_public_traffic": False}


async def test_a_burst_of_operations_makes_one_connection(e2b_provider, e2b_world):
    """Every provider operation used to open its own connection first.

    There are nineteen `_connect` call sites, and the browser view touches most
    of them, so opening the pane on a paused sandbox was measured making thirty
    requests to E2B -- nineteen of them this one, to the same sandbox, inside
    four seconds. Each is a round trip over the internet, so the wait a person
    reads as "the sandbox is slow" was mostly the platform talking to itself.

    Asserted as a count rather than a duration: a timing test would pass on a
    fast machine while the round trips were still being made.
    """
    from datetime import datetime, timedelta, timezone

    instance = await e2b_provider.create(_spec(uuid4()))
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    before = len(e2b_world.connect_timeouts)

    for _ in range(5):
        await e2b_provider.reach_port(instance, port=4850, deadline_at=deadline)

    spent = len(e2b_world.connect_timeouts) - before
    assert spent == 1, f"five operations opened {spent} connections"


async def test_the_lease_is_still_re_armed_when_the_hold_lapses(
    e2b_provider, e2b_world, monkeypatch
):
    """Holding a connection must not stop the sandbox lease being renewed.

    Connecting is what re-arms it, and a workspace whose lease runs out loses
    every process at once. So the hold is a short window, not a cache: once it
    lapses the next operation connects again and the lease moves with it.
    """
    from datetime import datetime, timedelta, timezone

    instance = await e2b_provider.create(_spec(uuid4()))
    deadline = datetime.now(timezone.utc) + timedelta(seconds=30)
    await e2b_provider.reach_port(instance, port=4850, deadline_at=deadline)
    before = len(e2b_world.connect_timeouts)

    # Lapse the hold rather than sleep through it.
    monkeypatch.setattr(e2b_provider._connections, "_rearm_seconds", -1.0)
    await e2b_provider.reach_port(instance, port=4850, deadline_at=deadline)

    assert len(e2b_world.connect_timeouts) == before + 1
    # And it carries the real lease, not the SDK's five-minute default.
    assert (
        e2b_world.connect_timeouts[-1] == e2b_provider._config.sandbox_timeout_seconds
    )
