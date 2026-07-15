from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace
from urllib import error as urlerror

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from agentbox.apps import sandbox_app  # noqa: E402
from agentbox.providers.cloud_config import (  # noqa: E402
    DaytonaProviderConfig,
    E2BProviderConfig,
)
from agentbox.providers.daytona import (  # noqa: E402
    DaytonaSandboxProvider,
    _DaytonaSdk,
)
from agentbox.providers.e2b import E2BSandboxProvider, _E2BSdk  # noqa: E402
from agentbox.providers.models import SandboxRef  # noqa: E402
from agentbox.schemas import SandboxEnsureRequest  # noqa: E402
from agentbox.providers.errors import (  # noqa: E402
    ProviderError,
    SandboxNotFoundError,
)


class _NotFound(Exception):
    pass


class _ProviderFailure(Exception):
    pass


class _RateLimit(Exception):
    pass


class _HealthResponse:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


def _healthy_urlopen(req, **kwargs):
    del kwargs
    url = str(getattr(req, "full_url", req))
    if "e2b.test" in url and not any(
        any(
            f"{provider_id}-{port}.e2b.test" in url
            for port in (8080, 8090)
        )
        and sandbox.running
        for provider_id, sandbox in _E2BSandbox.instances.items()
    ):
        raise urlerror.HTTPError(url, 502, "sandbox not found", {}, None)
    return _HealthResponse()


class _Query:
    def __init__(self, *, metadata=None, labels=None):
        self.filters = metadata or labels or {}


class _Paginator:
    def __init__(self, items, *, fail_after_first: bool = False):
        self.items = list(items)
        self.has_next = True
        self.fail_after_first = fail_after_first
        self.calls = 0

    async def next_items(self):
        self.calls += 1
        if self.fail_after_first and self.calls > 1:
            self.has_next = False
            raise _ProviderFailure("inventory page failed")
        self.has_next = self.fail_after_first
        return self.items


class _FakeCommands:
    def __init__(self, *, list_failures: int = 0) -> None:
        self.processes: list[SimpleNamespace] = []
        self.run_calls: list[dict[str, object]] = []
        self.list_failures = list_failures

    async def list(self):
        if self.list_failures:
            self.list_failures -= 1
            raise _ProviderFailure("commands data plane not ready")
        return list(self.processes)

    async def run(self, cmd, **kwargs):
        self.run_calls.append({"cmd": cmd, **kwargs})
        self.processes.append(SimpleNamespace(cmd=cmd, args=[]))
        return SimpleNamespace(exit_code=0 if "urlopen" in cmd else None)


class _E2BSandbox:
    infos: dict[str, SimpleNamespace] = {}
    instances: dict[str, "_E2BSandbox"] = {}
    create_count = 0
    connect_count = 0
    list_count = 0
    pause_count = 0
    fail_after_accept = False
    fail_lookup_after_accept = False
    fail_next_list = False
    fail_partial_next_list = False
    status_not_found_failures = 0
    bootstrap_list_failures = 0
    timeout_not_found_failures = 0
    status_false_failures = 0
    timeout_set_count = 0
    connect_not_found_failures = 0

    def __init__(self, sandbox_id: str) -> None:
        self.sandbox_id = sandbox_id
        self.running = True
        self.traffic_access_token = f"token-{sandbox_id}-1"
        self.timeout = 0
        self.commands = _FakeCommands(list_failures=type(self).bootstrap_list_failures)

    @classmethod
    def reset(cls) -> None:
        cls.infos = {}
        cls.instances = {}
        cls.create_count = 0
        cls.connect_count = 0
        cls.list_count = 0
        cls.pause_count = 0
        cls.fail_after_accept = False
        cls.fail_lookup_after_accept = False
        cls.fail_next_list = False
        cls.fail_partial_next_list = False
        cls.status_not_found_failures = 0
        cls.bootstrap_list_failures = 0
        cls.timeout_not_found_failures = 0
        cls.status_false_failures = 0
        cls.timeout_set_count = 0
        cls.connect_not_found_failures = 0

    @classmethod
    def list(cls, *, query, limit, **kwargs):
        del limit, kwargs
        cls.list_count += 1
        if cls.fail_next_list:
            cls.fail_next_list = False
            raise _ProviderFailure("inventory temporarily unavailable")
        items = [
            info
            for info in cls.infos.values()
            if all(
                info.metadata.get(key) == value for key, value in query.filters.items()
            )
        ]
        fail_partial = cls.fail_partial_next_list
        cls.fail_partial_next_list = False
        return _Paginator(items, fail_after_first=fail_partial)

    @classmethod
    async def create(cls, template, *, metadata, envs, **kwargs):
        del template, envs, kwargs
        cls.create_count += 1
        provider_id = f"e2b-{cls.create_count}"
        sandbox = cls(provider_id)
        cls.instances[provider_id] = sandbox
        cls.infos[provider_id] = SimpleNamespace(
            sandbox_id=provider_id,
            metadata=dict(metadata),
            state="running",
        )
        if cls.fail_after_accept or cls.fail_lookup_after_accept:
            if cls.fail_lookup_after_accept:
                cls.fail_lookup_after_accept = False
                cls.fail_next_list = True
            cls.fail_after_accept = False
            raise _ProviderFailure("accepted then disconnected")
        return sandbox

    @classmethod
    async def connect(cls, provider_id, **kwargs):
        del kwargs
        cls.connect_count += 1
        if cls.connect_not_found_failures:
            cls.connect_not_found_failures -= 1
            raise _NotFound
        try:
            sandbox = cls.instances[provider_id]
        except KeyError as exc:
            raise _NotFound from exc
        sandbox.running = True
        sandbox.commands = _FakeCommands()
        cls.infos[provider_id].state = "running"
        return sandbox

    @classmethod
    async def pause(cls, provider_id, *, keep_memory, **kwargs):
        del kwargs
        assert keep_memory is False
        cls.pause_count += 1
        try:
            sandbox = cls.instances[provider_id]
        except KeyError as exc:
            raise _NotFound from exc
        if not sandbox.running:
            return False
        sandbox.running = False
        cls.infos[provider_id].state = "paused"
        return True

    @classmethod
    async def kill(cls, provider_id, **kwargs):
        del kwargs
        if provider_id not in cls.instances:
            raise _NotFound
        cls.instances.pop(provider_id)
        cls.infos.pop(provider_id)
        return True

    async def is_running(self):
        if type(self).status_false_failures:
            type(self).status_false_failures -= 1
            return False
        if type(self).status_not_found_failures:
            type(self).status_not_found_failures -= 1
            raise _NotFound
        if self.sandbox_id not in type(self).instances:
            raise _NotFound
        return self.running

    async def set_timeout(self, timeout, **kwargs):
        del kwargs
        type(self).timeout_set_count += 1
        if type(self).timeout_not_found_failures:
            type(self).timeout_not_found_failures -= 1
            raise _NotFound
        self.timeout = timeout

    def get_host(self, port):
        return f"{self.sandbox_id}-{port}.e2b.test"


def _e2b_provider() -> E2BSandboxProvider:
    _E2BSandbox.reset()
    return E2BSandboxProvider(
        E2BProviderConfig(
            api_key="key",
            template="template",
            owner="tests",
            environment="unit",
            max_active=2,
        ),
        sdk=_E2BSdk(
            sandbox_cls=_E2BSandbox,
            query_cls=_Query,
            rate_limit_error=_RateLimit,
            not_found_error=_NotFound,
            sandbox_error=_ProviderFailure,
            urlopen=_healthy_urlopen,
        ),
    )


def _e2b_provider_with(**changes) -> E2BSandboxProvider:
    _E2BSandbox.reset()
    urlopen = changes.pop("urlopen", _healthy_urlopen)
    values = {
        "api_key": "key",
        "template": "template",
        "owner": "tests",
        "environment": "unit",
        "max_active": 2,
        "create_rate_per_second": 100,
        "status_retry_seconds": 0.01,
        **changes,
    }
    return E2BSandboxProvider(
        E2BProviderConfig(**values),
        sdk=_E2BSdk(
            sandbox_cls=_E2BSandbox,
            query_cls=_Query,
            rate_limit_error=_RateLimit,
            not_found_error=_NotFound,
            sandbox_error=_ProviderFailure,
            urlopen=urlopen,
        ),
    )


def _set_required_e2b_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("E2B_API_KEY", "test-key")
    monkeypatch.setenv("E2B_SANDBOX_TEMPLATE", "test-template")
    monkeypatch.setenv("AGENTBOX_PROVIDER_OWNER", "tests")
    monkeypatch.setenv("AGENTBOX_ENVIRONMENT", "unit")


def test_e2b_request_timeout_config_default_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_e2b_env(monkeypatch)
    monkeypatch.delenv("E2B_REQUEST_TIMEOUT_SECONDS", raising=False)

    assert E2BProviderConfig.from_env().request_timeout_seconds == 20

    monkeypatch.setenv("E2B_REQUEST_TIMEOUT_SECONDS", "7.5")
    assert E2BProviderConfig.from_env().request_timeout_seconds == 7.5


def test_e2b_sandbox_timeout_config_is_applied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_e2b_env(monkeypatch)
    monkeypatch.setenv("E2B_SANDBOX_TIMEOUT_SECONDS", "900")

    assert E2BProviderConfig.from_env().timeout_seconds == 900


def test_e2b_runtime_bootstrap_timeout_config_default_and_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _set_required_e2b_env(monkeypatch)
    monkeypatch.delenv("E2B_RUNTIME_BOOTSTRAP_TIMEOUT_SECONDS", raising=False)

    assert E2BProviderConfig.from_env().runtime_bootstrap_timeout_seconds == 120

    monkeypatch.setenv("E2B_RUNTIME_BOOTSTRAP_TIMEOUT_SECONDS", "180")
    assert E2BProviderConfig.from_env().runtime_bootstrap_timeout_seconds == 180


@pytest.mark.parametrize("value", ["0", "121"])
def test_e2b_request_timeout_config_validation(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _set_required_e2b_env(monkeypatch)
    monkeypatch.setenv("E2B_REQUEST_TIMEOUT_SECONDS", value)

    with pytest.raises(
        RuntimeError,
        match="E2B_REQUEST_TIMEOUT_SECONDS must be between 1 and 120",
    ):
        E2BProviderConfig.from_env()


@pytest.mark.parametrize("value", ["9", "301"])
def test_e2b_runtime_bootstrap_timeout_config_validation(
    monkeypatch: pytest.MonkeyPatch,
    value: str,
) -> None:
    _set_required_e2b_env(monkeypatch)
    monkeypatch.setenv("E2B_RUNTIME_BOOTSTRAP_TIMEOUT_SECONDS", value)

    with pytest.raises(
        RuntimeError,
        match="E2B_RUNTIME_BOOTSTRAP_TIMEOUT_SECONDS must be between 10 and 300",
    ):
        E2BProviderConfig.from_env()


def test_e2b_api_options_include_numeric_request_timeout() -> None:
    provider = _e2b_provider_with(request_timeout_seconds=17.5)

    options = provider._api_options()

    assert options["request_timeout"] == 17.5
    assert isinstance(options["request_timeout"], float)


def test_e2b_contract_is_idempotent_and_refreshes_endpoint_token() -> None:
    provider = _e2b_provider()
    request = SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://lemma.test"})

    first = asyncio.run(provider.create("sandbox-1", request))
    asyncio.run(provider.bootstrap("sandbox-1", request))
    second = asyncio.run(provider.create("sandbox-1", request))
    asyncio.run(provider.bootstrap("sandbox-1", request))
    endpoint_one = asyncio.run(
        provider.resolve_endpoint("sandbox-1", sandbox_app("runtime"))
    )
    sandbox = _E2BSandbox.instances[endpoint_one.instance_id or ""]
    sandbox.traffic_access_token = "refreshed-token"
    endpoint_two = asyncio.run(
        provider.resolve_endpoint("sandbox-1", sandbox_app("runtime"))
    )
    inventory = asyncio.run(provider.list_managed())

    assert first.ready and second.ready
    assert _E2BSandbox.create_count == 1
    assert (
        next(iter(_E2BSandbox.infos.values())).metadata["agentbox-environment"]
        == "unit"
    )
    assert len(sandbox.commands.run_calls) == 3
    assert sandbox.commands.run_calls[0]["envs"] == {
        "LEMMA_BASE_URL": "https://lemma.test",
        "PYTHONPATH": "/app",
    }
    assert sandbox.commands.run_calls[0]["user"] == "appuser"
    assert all("urlopen" in call["cmd"] for call in sandbox.commands.run_calls[1:])
    assert (
        endpoint_one.headers["e2b-traffic-access-token"]
        != endpoint_two.headers["e2b-traffic-access-token"]
    )
    assert endpoint_two.headers["e2b-traffic-access-token"] == "refreshed-token"
    assert endpoint_two.transient_gateway == "e2b"
    assert inventory[0].ref.provider_id == endpoint_two.instance_id
    assert endpoint_two.provider_id == inventory[0].ref.provider_id
    assert asyncio.run(provider.delete("sandbox-1")) is True
    assert asyncio.run(provider.delete("sandbox-1")) is False


def test_e2b_endpoint_refresh_retries_direct_connect_without_inventory() -> None:
    provider = _e2b_provider_with(status_retry_seconds=1)
    request = SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://lemma.test"})
    asyncio.run(provider.create("sandbox-1", request))
    original = asyncio.run(
        provider.resolve_endpoint("sandbox-1", sandbox_app("runtime"))
    )
    list_count = _E2BSandbox.list_count
    _E2BSandbox.connect_not_found_failures = 1

    refreshed = asyncio.run(
        provider.refresh_endpoint(
            "sandbox-1",
            sandbox_app("runtime"),
            instance_id=original.instance_id or "",
        )
    )

    assert refreshed.instance_id == original.instance_id
    assert _E2BSandbox.connect_count == 2
    assert _E2BSandbox.list_count == list_count
    assert original.instance_id in _E2BSandbox.instances
    assert _E2BSandbox.pause_count == 0


def test_e2b_fresh_live_generation_survives_empty_inventory_lag() -> None:
    provider = _e2b_provider_with(status_retry_seconds=1)
    request = SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://lemma.test"})
    asyncio.run(provider.create("sandbox-1", request))
    asyncio.run(provider.bootstrap("sandbox-1", request))
    provider_id = provider._known_infos["sandbox-1"].provider_id

    # E2B's list API can lag a successful create while the authenticated data
    # plane is already healthy. Keep the exact cached generation during the
    # bounded grace instead of turning this into a false not-found.
    _E2BSandbox.infos.pop(provider_id)
    inventory = asyncio.run(provider.list_managed())

    endpoint = asyncio.run(
        provider.resolve_endpoint("sandbox-1", sandbox_app("runtime"))
    )
    assert [item.ref.provider_id for item in inventory] == [provider_id]
    assert endpoint.instance_id == provider_id
    assert provider._known_infos["sandbox-1"].provider_id == provider_id
    assert provider._sandboxes["sandbox-1"].sandbox_id == provider_id


def test_e2b_adopts_durable_generation_without_inventory_lookup() -> None:
    provider = _e2b_provider_with(status_retry_seconds=1)
    request = SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://lemma.test"})
    asyncio.run(provider.create("sandbox-1", request))
    provider_id = provider._known_infos["sandbox-1"].provider_id
    list_count = _E2BSandbox.list_count

    provider.invalidate_sandbox_cache("sandbox-1")
    assert asyncio.run(provider.adopt("sandbox-1", provider_id)) is True
    assert _E2BSandbox.list_count == list_count

    asyncio.run(provider.create("sandbox-1", request))
    assert _E2BSandbox.create_count == 1
    assert provider._known_infos["sandbox-1"].provider_id == provider_id


def test_e2b_generation_token_never_adopts_older_logical_match() -> None:
    provider = _e2b_provider_with(status_retry_seconds=0.01)
    request = SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://lemma.test"})
    asyncio.run(provider.create("sandbox-1", request))
    old_provider_id = provider._known_infos["sandbox-1"].provider_id
    provider.invalidate_sandbox_cache("sandbox-1")
    _E2BSandbox.fail_lookup_after_accept = True

    with pytest.raises(ProviderError) as unknown:
        asyncio.run(
            provider.create_generation(
                "sandbox-1",
                request,
                generation_token="durable-attempt-2",
            )
        )

    assert unknown.value.code == "provider_create_outcome_unknown"
    assert _E2BSandbox.create_count == 2
    new_provider_id = next(
        provider_id
        for provider_id, info in _E2BSandbox.infos.items()
        if info.metadata.get("agentbox-generation") == "durable-attempt-2"
    )
    assert new_provider_id != old_provider_id

    status = asyncio.run(
        provider.create_generation(
            "sandbox-1",
            request,
            generation_token="durable-attempt-2",
        )
    )
    assert status.ready is True
    assert _E2BSandbox.create_count == 2
    assert provider._known_infos["sandbox-1"].provider_id == new_provider_id


def test_e2b_endpoint_refresh_reuses_exact_cached_route_during_control_plane_lag() -> (
    None
):
    provider = _e2b_provider_with(status_retry_seconds=0.01)
    request = SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://lemma.test"})
    asyncio.run(provider.create("sandbox-1", request))
    original = asyncio.run(
        provider.resolve_endpoint("sandbox-1", sandbox_app("runtime"))
    )
    list_count = _E2BSandbox.list_count
    _E2BSandbox.connect_not_found_failures = 100

    refreshed = asyncio.run(
        provider.refresh_endpoint(
            "sandbox-1",
            sandbox_app("runtime"),
            instance_id=original.instance_id or "",
        )
    )

    assert refreshed.instance_id == original.instance_id
    assert refreshed.base_url == original.base_url
    assert _E2BSandbox.list_count == list_count
    assert original.instance_id in _E2BSandbox.instances
    assert _E2BSandbox.pause_count == 0


def test_e2b_bootstrap_waits_for_public_data_plane_after_local_health() -> None:
    calls = 0

    def transient_gateway(*args, **kwargs):
        nonlocal calls
        del args, kwargs
        calls += 1
        if calls == 1:
            raise urlerror.HTTPError(
                "https://runtime.e2b.test/health",
                502,
                "sandbox not found",
                {},
                None,
            )
        return _HealthResponse()

    provider = _e2b_provider_with(urlopen=transient_gateway)
    request = SandboxEnsureRequest()
    status = asyncio.run(provider.create("sandbox-1", request))
    asyncio.run(provider.bootstrap("sandbox-1", request))

    assert status.ready is True
    # The first runtime route check fails. The next pass verifies both the
    # runtime (8080) and function executor (8090) before publishing readiness.
    assert calls == 3


def test_e2b_bootstrap_requires_public_function_executor_readiness() -> None:
    def function_executor_missing(req, **kwargs):
        del kwargs
        if "-8090." in str(getattr(req, "full_url", req)):
            raise urlerror.HTTPError(
                str(getattr(req, "full_url", req)),
                502,
                "port is not open",
                {},
                None,
            )
        return _HealthResponse()

    provider = _e2b_provider_with(
        urlopen=function_executor_missing,
        runtime_bootstrap_timeout_seconds=0.01,
    )
    request = SandboxEnsureRequest()
    asyncio.run(provider.create("sandbox-1", request))

    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.bootstrap("sandbox-1", request))

    assert error.value.code == "runtime_bootstrap_failed"


def test_e2b_bootstrap_retries_transient_commands_data_plane() -> None:
    provider = _e2b_provider_with(runtime_bootstrap_timeout_seconds=1)
    _E2BSandbox.bootstrap_list_failures = 1

    request = SandboxEnsureRequest()
    status = asyncio.run(provider.create("sandbox-1", request))
    asyncio.run(provider.bootstrap("sandbox-1", request))

    assert status.ready is True
    sandbox = next(iter(_E2BSandbox.instances.values()))
    assert sandbox.commands.list_failures == 0


def test_e2b_bootstrap_fails_closed_when_commands_data_plane_never_arrives() -> None:
    provider = _e2b_provider_with(runtime_bootstrap_timeout_seconds=0.01)
    _E2BSandbox.bootstrap_list_failures = 100

    request = SandboxEnsureRequest()
    asyncio.run(provider.create("sandbox-1", request))
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.bootstrap("sandbox-1", request))

    assert error.value.code == "runtime_bootstrap_failed"
    assert len(_E2BSandbox.instances) == 1


def test_e2b_bootstrap_fails_closed_when_public_data_plane_never_arrives() -> None:
    def missing_gateway(*args, **kwargs):
        del args, kwargs
        raise urlerror.HTTPError(
            "https://runtime.e2b.test/health",
            502,
            "sandbox not found",
            {},
            None,
        )

    provider = _e2b_provider_with(
        urlopen=missing_gateway,
        runtime_bootstrap_timeout_seconds=0.01,
    )

    request = SandboxEnsureRequest()
    asyncio.run(provider.create("sandbox-1", request))
    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.bootstrap("sandbox-1", request))

    assert error.value.code == "runtime_bootstrap_failed"
    assert len(_E2BSandbox.instances) == 1


def test_e2b_status_reconnects_same_provider_after_transient_not_found() -> None:
    public_ready = True

    def conditional_gateway(req, **kwargs):
        del kwargs
        if public_ready:
            return _healthy_urlopen(req)
        raise urlerror.HTTPError(
            str(getattr(req, "full_url", req)),
            502,
            "sandbox not found",
            {},
            None,
        )

    provider = _e2b_provider_with(
        status_retry_seconds=1,
        urlopen=conditional_gateway,
    )
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = next(iter(_E2BSandbox.instances))
    connects_before = _E2BSandbox.connect_count
    _E2BSandbox.status_not_found_failures = 1
    public_ready = False

    status = asyncio.run(provider.get_status("sandbox-1"))

    assert status.ready is True
    assert _E2BSandbox.connect_count == connects_before + 1
    assert next(iter(_E2BSandbox.instances)) == provider_id


def test_e2b_status_uses_authenticated_data_plane_when_control_plane_flaps() -> None:
    provider = _e2b_provider_with(status_retry_seconds=1)
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = next(iter(_E2BSandbox.instances))
    _E2BSandbox.status_not_found_failures = 100

    status = asyncio.run(provider.get_status("sandbox-1"))

    assert status.ready is True
    assert provider._known_infos["sandbox-1"].provider_id == provider_id
    assert provider._sandboxes["sandbox-1"].sandbox_id == provider_id


def test_e2b_status_uses_data_plane_when_control_plane_temporarily_says_stopped() -> (
    None
):
    provider = _e2b_provider_with(status_retry_seconds=1)
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    _E2BSandbox.status_false_failures = 1

    status = asyncio.run(provider.get_status("sandbox-1"))

    assert status.ready is True
    assert status.status == "RUNNING"


def test_e2b_status_fails_when_control_and_data_planes_are_unavailable() -> None:
    data_plane_available = True

    def conditional_gateway(*args, **kwargs):
        del args, kwargs
        if data_plane_available:
            return _HealthResponse()
        raise urlerror.HTTPError(
            "https://runtime.e2b.test/health",
            502,
            "sandbox not found",
            {},
            None,
        )

    provider = _e2b_provider_with(
        urlopen=conditional_gateway,
        status_retry_seconds=0.01,
    )
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    _E2BSandbox.status_not_found_failures = 100
    data_plane_available = False

    with pytest.raises(ProviderError) as error:
        asyncio.run(provider.get_status("sandbox-1"))

    assert error.value.code == "provider_adoption_unavailable"
    assert error.value.status_code == 503
    assert error.value.retryable is True
    assert provider._known_infos["sandbox-1"].provider_id == "e2b-1"
    assert "sandbox-1" not in provider._sandboxes


def test_e2b_existing_ensure_accepts_data_plane_when_timeout_renewal_flaps() -> None:
    provider = _e2b_provider_with(status_retry_seconds=1)
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = next(iter(_E2BSandbox.instances))
    _E2BSandbox.timeout_not_found_failures = 1

    status = asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))

    assert status.ready is True
    assert next(iter(_E2BSandbox.instances)) == provider_id
    assert _E2BSandbox.create_count == 1


def test_e2b_lease_renewal_is_throttled_after_create() -> None:
    provider = _e2b_provider_with(timeout_seconds=90)

    async def scenario() -> None:
        await provider.create("sandbox-1", SandboxEnsureRequest())
        await provider.renew_lease("sandbox-1")
        await provider.renew_lease("sandbox-1")

    asyncio.run(scenario())

    assert _E2BSandbox.timeout_set_count == 0


def test_e2b_concurrent_lease_renewals_make_one_provider_call() -> None:
    provider = _e2b_provider_with(timeout_seconds=90)

    async def scenario() -> None:
        await provider.create("sandbox-1", SandboxEnsureRequest())
        provider._lease_renewed_at["sandbox-1"] -= 31
        await asyncio.gather(*(provider.renew_lease("sandbox-1") for _ in range(10)))

    asyncio.run(scenario())

    assert _E2BSandbox.timeout_set_count == 1


def test_e2b_lease_renews_again_after_timeout_third() -> None:
    provider = _e2b_provider_with(timeout_seconds=90)

    async def scenario() -> None:
        await provider.create("sandbox-1", SandboxEnsureRequest())
        first_created_at = provider._lease_renewed_at["sandbox-1"]
        provider._lease_renewed_at["sandbox-1"] -= 31
        await provider.renew_lease("sandbox-1")
        first_renewed_at = provider._lease_renewed_at["sandbox-1"]
        await provider.renew_lease("sandbox-1")
        assert first_renewed_at > first_created_at
        provider._lease_renewed_at["sandbox-1"] -= 31
        await provider.renew_lease("sandbox-1")

    asyncio.run(scenario())

    assert _E2BSandbox.timeout_set_count == 2


def test_e2b_endpoint_cache_avoids_inventory_but_refreshes_token() -> None:
    provider = _e2b_provider_with()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    inventory_lookups = _E2BSandbox.list_count

    first = asyncio.run(provider.resolve_endpoint("sandbox-1", sandbox_app("runtime")))
    _E2BSandbox.instances[first.instance_id or ""].traffic_access_token = "fresh"
    second = asyncio.run(provider.resolve_endpoint("sandbox-1", sandbox_app("runtime")))

    assert _E2BSandbox.list_count == inventory_lookups
    assert first.headers["e2b-traffic-access-token"] != "fresh"
    assert second.headers["e2b-traffic-access-token"] == "fresh"


def test_e2b_release_preserves_provider_id_and_does_not_resume_on_status() -> None:
    provider = _e2b_provider_with(max_active=1)

    first = asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = next(iter(_E2BSandbox.instances))
    assert first.ready
    assert asyncio.run(provider.release("sandbox-1")) is True
    connects_after_release = _E2BSandbox.connect_count

    status = asyncio.run(provider.get_status("sandbox-1"))
    inventory = asyncio.run(provider.list_managed())
    assert status.status == "STOPPED"
    assert inventory[0].status.status == "STOPPED"
    assert _E2BSandbox.connect_count == connects_after_release
    assert provider._observed_active_count == 0

    resumed = asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert resumed.ready
    assert next(iter(_E2BSandbox.instances)) == provider_id
    assert _E2BSandbox.create_count == 1


def test_e2b_resume_retries_only_exact_id_during_connect_lag() -> None:
    provider = _e2b_provider_with(status_retry_seconds=1)
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = next(iter(_E2BSandbox.instances))
    assert asyncio.run(provider.release("sandbox-1")) is True
    _E2BSandbox.connect_not_found_failures = 2

    resumed = asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))

    assert resumed.ready is True
    assert next(iter(_E2BSandbox.instances)) == provider_id
    assert _E2BSandbox.create_count == 1


def test_e2b_release_frees_capacity_for_another_sandbox() -> None:
    provider = _e2b_provider_with(max_active=1, admission_wait_seconds=0.05)
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    asyncio.run(provider.release("sandbox-1"))

    second = asyncio.run(provider.create("sandbox-2", SandboxEnsureRequest()))

    assert second.ready
    assert provider._observed_active_count == 1


def test_e2b_explicit_delete_purges_provider_identity() -> None:
    provider = _e2b_provider_with()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    original_id = next(iter(_E2BSandbox.instances))
    asyncio.run(provider.release("sandbox-1"))

    assert asyncio.run(provider.delete("sandbox-1")) is True
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))

    assert next(iter(_E2BSandbox.instances)) != original_id


def test_e2b_exact_purge_converges_when_provider_id_is_already_absent() -> None:
    provider = _e2b_provider_with()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = next(iter(_E2BSandbox.instances))
    _E2BSandbox.instances.pop(provider_id)
    _E2BSandbox.infos.pop(provider_id)

    deleted = asyncio.run(
        provider.purge_managed(SandboxRef("sandbox-1", provider_id))
    )

    assert deleted is True
    assert "sandbox-1" not in provider._known_infos
    assert "sandbox-1" not in provider._sandboxes


def test_e2b_logical_delete_converges_when_exact_kill_returns_not_found() -> None:
    provider = _e2b_provider_with()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = next(iter(_E2BSandbox.instances))
    _E2BSandbox.instances.pop(provider_id)

    assert asyncio.run(provider.delete("sandbox-1")) is True
    assert "sandbox-1" not in provider._known_infos
    assert "sandbox-1" not in provider._sandboxes


def test_e2b_capacity_timeout_is_retryable_and_exact() -> None:
    provider = _e2b_provider_with(
        max_active=1,
        admission_wait_seconds=0.01,
        capacity_retry_after_seconds=23,
    )
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))

    try:
        asyncio.run(provider.create("sandbox-2", SandboxEnsureRequest()))
    except ProviderError as exc:
        assert exc.status_code == 429
        assert exc.retryable is True
        assert exc.headers == {"Retry-After": "23"}
    else:
        raise AssertionError("capacity admission unexpectedly succeeded")

    assert _E2BSandbox.create_count == 1
    assert provider._observed_active_count == 1
    assert provider._capacity_reservations == set()


def test_e2b_concurrent_ensure_is_idempotent() -> None:
    provider = _e2b_provider_with()

    async def scenario() -> None:
        statuses = await asyncio.gather(
            provider.create("sandbox-1", SandboxEnsureRequest()),
            provider.create("sandbox-1", SandboxEnsureRequest()),
        )
        assert all(status.ready for status in statuses)

    asyncio.run(scenario())
    assert _E2BSandbox.create_count == 1


def test_e2b_post_create_status_failure_keeps_exact_attempt_for_recovery() -> None:
    provider = _e2b_provider_with()
    original_status = provider._status

    async def fail_status(*args, **kwargs):
        del args, kwargs
        raise _ProviderFailure("status data plane unavailable")

    provider._status = fail_status  # type: ignore[method-assign]
    with pytest.raises(ProviderError) as error:
        asyncio.run(
            provider.create_generation(
                "sandbox-1",
                SandboxEnsureRequest(),
                generation_token="durable-attempt-1",
            )
        )

    assert error.value.code == "provider_create_outcome_unknown"
    assert _E2BSandbox.create_count == 1
    provider_id = next(iter(_E2BSandbox.instances))
    assert provider._known_infos["sandbox-1"].provider_id == provider_id
    assert (
        provider._known_infos["sandbox-1"].metadata["agentbox-generation"]
        == "durable-attempt-1"
    )

    provider._status = original_status  # type: ignore[method-assign]
    recovered = asyncio.run(
        provider.create_generation(
            "sandbox-1",
            SandboxEnsureRequest(),
            generation_token="durable-attempt-1",
        )
    )

    assert recovered.ready is True
    assert _E2BSandbox.create_count == 1


def test_e2b_concurrent_capacity_does_not_oversubscribe() -> None:
    provider = _e2b_provider_with(
        max_active=1,
        admission_wait_seconds=0.01,
    )

    async def scenario() -> list[object]:
        return await asyncio.gather(
            provider.create("sandbox-1", SandboxEnsureRequest()),
            provider.create("sandbox-2", SandboxEnsureRequest()),
            return_exceptions=True,
        )

    results = asyncio.run(scenario())
    errors = [result for result in results if isinstance(result, ProviderError)]
    assert len(errors) == 1
    assert errors[0].retryable is True
    assert _E2BSandbox.create_count == 1


def test_e2b_provider_retry_after_is_respected(monkeypatch) -> None:
    provider = _e2b_provider_with()
    attempts = 0
    delays: list[float] = []

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            error = _RateLimit()
            error.headers = {"Retry-After": "7"}  # type: ignore[attr-defined]
            raise error
        return "ok"

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    provider._sleep = record_sleep  # type: ignore[method-assign]
    monkeypatch.setattr("agentbox.providers.e2b.random.uniform", lambda *_: 0.0)

    assert asyncio.run(provider._with_rate_limit_retry(operation)) == "ok"
    assert delays == [7.0]


def test_e2b_provider_rate_limit_without_header_uses_short_backoff(
    monkeypatch,
) -> None:
    provider = _e2b_provider_with(capacity_retry_after_seconds=23)
    attempts = 0
    delays: list[float] = []

    async def operation():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise _RateLimit
        return "ok"

    async def record_sleep(delay: float) -> None:
        delays.append(delay)

    provider._sleep = record_sleep  # type: ignore[method-assign]
    monkeypatch.setattr("agentbox.providers.e2b.random.uniform", lambda *_: 0.0)

    assert asyncio.run(provider._with_rate_limit_retry(operation)) == "ok"
    assert delays == [0.5]


def test_e2b_bootstrap_failure_is_separate_from_compute_create() -> None:
    provider = _e2b_provider_with(max_active=1)

    async def fail_bootstrap(sandbox, env):
        del sandbox, env
        raise RuntimeError("bootstrap failed")

    provider._bootstrap_sandbox = fail_bootstrap  # type: ignore[method-assign]
    request = SandboxEnsureRequest()
    asyncio.run(provider.create("sandbox-1", request))
    with pytest.raises(RuntimeError, match="bootstrap failed"):
        asyncio.run(provider.bootstrap("sandbox-1", request))
    assert len(_E2BSandbox.instances) == 1
    assert asyncio.run(provider.delete("sandbox-1")) is True
    assert _E2BSandbox.instances == {}
    assert provider._observed_active_count == 0
    assert provider._capacity_reservations == set()


def test_e2b_create_rate_slots_are_spaced() -> None:
    provider = _e2b_provider_with(create_rate_per_second=2)
    sleeps: list[float] = []

    async def record_sleep(delay: float) -> None:
        sleeps.append(delay)

    provider._clock = lambda: 100.0  # type: ignore[method-assign]
    provider._sleep = record_sleep  # type: ignore[method-assign]

    asyncio.run(provider._wait_for_create_rate_slot())
    asyncio.run(provider._wait_for_create_rate_slot())
    asyncio.run(provider._wait_for_create_rate_slot())

    assert sleeps == [0.5, 1.0]


class _Params:
    def __init__(self, **kwargs):
        self.values = kwargs


class _DaytonaSandbox:
    def __init__(self, sandbox_id: str, labels: dict[str, str]) -> None:
        self.id = sandbox_id
        self.labels = labels
        self.state = "started"
        self.updated_at = "generation-1"
        self.preview_count = 0

    async def recover(self, timeout=None):
        del timeout
        self.state = "started"

    async def get_preview_link(self, port: int):
        self.preview_count += 1
        return SimpleNamespace(
            url=f"https://{self.id}-{port}.daytona.test",
            token=f"preview-{self.preview_count}",
        )


class _DaytonaClient:
    def __init__(self) -> None:
        self.sandboxes: dict[str, _DaytonaSandbox] = {}
        self.created_params = None
        self.create_count = 0
        self.list_count = 0
        self.fail_next_create = False
        self.fail_after_accept = False
        self.fail_lookup_after_accept = False
        self.fail_status_after_accept: int | None = None
        self.fail_next_list = False
        self.fail_partial_next_list = False
        self.fail_next_start_not_found = False

    async def get(self, provider_id):
        try:
            return self.sandboxes[provider_id]
        except KeyError as exc:
            raise _NotFound from exc

    async def list(self, query):
        self.list_count += 1
        if self.fail_next_list:
            self.fail_next_list = False
            raise _ProviderFailure("inventory temporarily unavailable")
        yielded = False
        fail_partial = self.fail_partial_next_list
        self.fail_partial_next_list = False
        for sandbox in list(self.sandboxes.values()):
            if all(
                sandbox.labels.get(key) == value for key, value in query.filters.items()
            ):
                yielded = True
                yield sandbox
                if fail_partial:
                    raise _ProviderFailure("inventory page failed")
        if fail_partial and not yielded:
            raise _ProviderFailure("inventory page failed")

    async def create(self, params, *, timeout):
        del timeout
        if self.fail_next_create:
            self.fail_next_create = False
            raise _ProviderFailure("create failed")
        self.created_params = params
        self.create_count += 1
        sandbox = _DaytonaSandbox(
            f"daytona-{self.create_count}", params.values["labels"]
        )
        self.sandboxes[sandbox.id] = sandbox
        if self.fail_after_accept or self.fail_lookup_after_accept:
            if self.fail_lookup_after_accept:
                self.fail_lookup_after_accept = False
                self.fail_next_list = True
            self.fail_after_accept = False
            error = _ProviderFailure("accepted then disconnected")
            if self.fail_status_after_accept is not None:
                error.status_code = self.fail_status_after_accept  # type: ignore[attr-defined]
                self.fail_status_after_accept = None
            raise error
        return sandbox

    async def start(self, sandbox, *, timeout):
        del timeout
        if self.fail_next_start_not_found:
            self.fail_next_start_not_found = False
            self.sandboxes.pop(sandbox.id, None)
            raise _NotFound
        sandbox.state = "started"
        sandbox.updated_at = "generation-resumed"

    async def stop(self, sandbox, *, timeout):
        del timeout
        sandbox.state = "stopped"
        sandbox.updated_at = "generation-stopped"

    async def delete(self, sandbox):
        self.sandboxes.pop(sandbox.id, None)

    async def close(self):
        return None


def _daytona_provider(**changes) -> tuple[DaytonaSandboxProvider, _DaytonaClient]:
    client = _DaytonaClient()
    sdk = _DaytonaSdk(
        client_cls=None,
        config_cls=None,
        query_cls=_Query,
        snapshot_params_cls=_Params,
        image_params_cls=_Params,
        error=_ProviderFailure,
        not_found_error=_NotFound,
    )
    values = {
        "api_key": "key",
        "owner": "tests",
        "environment": "unit",
        "snapshot": "snapshot",
        "max_active": 2,
        "create_rate_per_second": 100,
        "allow_unsafe_private_egress": True,
        **changes,
    }
    provider = DaytonaSandboxProvider(
        DaytonaProviderConfig(**values),
        sdk=sdk,
        client=client,
    )
    return provider, client


def test_daytona_contract_uses_async_client_and_refreshes_preview_token() -> None:
    provider, client = _daytona_provider()
    request = SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://lemma.test"})

    status = asyncio.run(provider.create("sandbox-1", request))
    endpoint_one = asyncio.run(
        provider.resolve_endpoint("sandbox-1", sandbox_app("browser"))
    )
    sandbox = next(iter(client.sandboxes.values()))
    sandbox.updated_at = "generation-2"
    endpoint_two = asyncio.run(
        provider.resolve_endpoint("sandbox-1", sandbox_app("browser"))
    )
    inventory = asyncio.run(provider.list_managed())

    assert status.ready
    assert client.created_params.values["snapshot"] == "snapshot"
    assert client.created_params.values["labels"]["agentbox-id"] == "sandbox-1"
    assert client.created_params.values["labels"]["agentbox-environment"] == "unit"
    assert endpoint_one.headers["X-Daytona-Preview-Token"] == "preview-1"
    assert endpoint_two.headers["X-Daytona-Preview-Token"] == "preview-2"
    assert endpoint_one.instance_id != endpoint_two.instance_id
    assert inventory[0].instance_id == endpoint_two.instance_id
    assert asyncio.run(provider.delete("sandbox-1")) is True
    assert asyncio.run(provider.delete("sandbox-1")) is False


def test_daytona_endpoint_cache_avoids_inventory_but_refreshes_token() -> None:
    provider, client = _daytona_provider()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    inventory_lookups = client.list_count

    first = asyncio.run(provider.resolve_endpoint("sandbox-1", sandbox_app("browser")))
    second = asyncio.run(provider.resolve_endpoint("sandbox-1", sandbox_app("browser")))

    assert client.list_count == inventory_lookups
    assert first.headers["X-Daytona-Preview-Token"] == "preview-1"
    assert second.headers["X-Daytona-Preview-Token"] == "preview-2"


def test_daytona_concurrent_ensure_is_idempotent() -> None:
    provider, client = _daytona_provider()

    async def scenario() -> None:
        statuses = await asyncio.gather(
            provider.create("sandbox-1", SandboxEnsureRequest()),
            provider.create("sandbox-1", SandboxEnsureRequest()),
        )
        assert all(status.ready for status in statuses)

    asyncio.run(scenario())
    assert client.create_count == 1


def test_daytona_exact_purge_converges_when_provider_id_is_already_absent() -> None:
    provider, client = _daytona_provider()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = next(iter(client.sandboxes))
    client.sandboxes.pop(provider_id)
    provider.invalidate_sandbox_cache("sandbox-1")

    deleted = asyncio.run(
        provider.purge_managed(SandboxRef("sandbox-1", provider_id))
    )

    assert deleted is True
    assert "sandbox-1" not in provider._sandboxes


def test_daytona_capacity_timeout_is_retryable_and_exact() -> None:
    provider, client = _daytona_provider(
        max_active=1,
        admission_wait_seconds=0.01,
        capacity_retry_after_seconds=19,
    )
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))

    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.create("sandbox-2", SandboxEnsureRequest()))

    assert caught.value.status_code == 429
    assert caught.value.retryable is True
    assert caught.value.headers == {"Retry-After": "19"}
    assert client.create_count == 1


def test_daytona_creation_failure_keeps_ambiguous_capacity_fenced() -> None:
    provider, client = _daytona_provider(max_active=1)
    client.fail_next_create = True

    with pytest.raises(ProviderError):
        asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))

    assert provider._observed_active_count == 0
    assert provider._capacity_reservations == {"sandbox-1"}
    assert provider._ambiguous_capacity_reservations == {"sandbox-1"}
    with pytest.raises(ProviderError) as capacity:
        asyncio.run(provider.create("sandbox-2", SandboxEnsureRequest()))
    assert capacity.value.code == "capacity_exhausted"


def test_daytona_release_preserves_provider_id_and_status_does_not_resume() -> None:
    provider, client = _daytona_provider(max_active=1)
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = next(iter(client.sandboxes))

    assert asyncio.run(provider.release("sandbox-1")) is True
    lookups_after_release = client.list_count
    assert asyncio.run(provider.get_status("sandbox-1")).status == "STOPPED"
    assert asyncio.run(provider.list_managed())[0].status.status == "STOPPED"
    # Status revalidates the cached identity, and list_managed performs its own
    # complete inventory without resuming the stopped sandbox.
    assert client.list_count == lookups_after_release + 2
    assert provider._observed_active_count == 0

    assert asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest())).ready
    assert next(iter(client.sandboxes)) == provider_id
    assert client.create_count == 1


def test_daytona_delete_missing_refreshes_capacity_and_wakes_waiter() -> None:
    provider, client = _daytona_provider(
        max_active=1,
        admission_wait_seconds=1,
    )

    async def scenario() -> None:
        await provider.create("sandbox-1", SandboxEnsureRequest())
        client.sandboxes.clear()
        provider.invalidate_sandbox_cache("sandbox-1")
        waiting = asyncio.create_task(
            provider.create("sandbox-2", SandboxEnsureRequest())
        )
        await asyncio.sleep(0.01)
        assert not waiting.done()
        assert await provider.delete("sandbox-1") is False
        assert (await asyncio.wait_for(waiting, timeout=1)).ready

    asyncio.run(scenario())
    assert provider._observed_active_count == 1


def test_daytona_capacity_waiter_wakes_after_delete() -> None:
    provider, client = _daytona_provider(
        max_active=1,
        admission_wait_seconds=1,
    )

    async def scenario() -> None:
        await provider.create("sandbox-1", SandboxEnsureRequest())
        waiting = asyncio.create_task(
            provider.create("sandbox-2", SandboxEnsureRequest())
        )
        await asyncio.sleep(0.01)
        assert not waiting.done()
        assert await provider.delete("sandbox-1") is True
        status = await asyncio.wait_for(waiting, timeout=1)
        assert status.id == "sandbox-2"

    asyncio.run(scenario())

    assert client.create_count == 2
    assert provider._observed_active_count == 1
    assert provider._capacity_reservations == set()


def test_e2b_adopts_create_accepted_before_transport_failure() -> None:
    provider = _e2b_provider_with()
    _E2BSandbox.fail_after_accept = True
    status = asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert status.ready
    assert _E2BSandbox.create_count == 1
    assert len(asyncio.run(provider.list_managed())) == 1


def test_daytona_fails_closed_without_network_policy_or_explicit_override() -> None:
    with pytest.raises(RuntimeError, match="private-network egress denial"):
        DaytonaSandboxProvider(
            DaytonaProviderConfig(
                api_key="key",
                owner="tests",
                environment="unit",
                snapshot="snapshot",
            ),
            sdk=_DaytonaSdk(
                client_cls=None,
                config_cls=None,
                query_cls=_Query,
                snapshot_params_cls=_Params,
                image_params_cls=_Params,
                error=_ProviderFailure,
                not_found_error=_NotFound,
            ),
            client=_DaytonaClient(),
        )


def test_daytona_enforces_configured_egress_policy() -> None:
    provider, client = _daytona_provider(
        allow_unsafe_private_egress=False,
        network_allow_list=("0.0.0.0/0",),
        domain_allow_list=("api.lemma.work",),
    )
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert client.created_params.values["network_block_all"] is True
    assert client.created_params.values["network_allow_list"] == "0.0.0.0/0"
    assert client.created_params.values["domain_allow_list"] == "api.lemma.work"
    assert provider.capabilities.private_egress_isolation is True


def test_daytona_adopts_create_accepted_before_transport_failure() -> None:
    provider, client = _daytona_provider()
    client.fail_after_accept = True
    status = asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert status.ready
    assert client.create_count == 1
    assert len(asyncio.run(provider.list_managed())) == 1


@pytest.mark.parametrize("status_code", [404, 429])
def test_daytona_post_acceptance_4xx_keeps_exact_attempt(status_code: int) -> None:
    provider, client = _daytona_provider()
    client.fail_lookup_after_accept = True
    client.fail_status_after_accept = status_code

    with pytest.raises(ProviderError) as caught:
        asyncio.run(
            provider.create_generation(
                "sandbox-1",
                SandboxEnsureRequest(),
                generation_token="durable-attempt-1",
            )
        )

    assert caught.value.code == "provider_create_outcome_unknown"
    assert client.create_count == 1
    recovered = asyncio.run(
        provider.create_generation(
            "sandbox-1",
            SandboxEnsureRequest(),
            generation_token="durable-attempt-1",
        )
    )
    assert recovered.ready is True
    assert client.create_count == 1


def test_e2b_inventory_reconciles_ambiguous_local_reservation() -> None:
    provider = _e2b_provider_with(max_active=1, admission_wait_seconds=0.01)
    _E2BSandbox.fail_lookup_after_accept = True

    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert caught.value.code == "provider_create_outcome_unknown"
    assert provider._capacity_reservations == {"sandbox-1"}
    assert provider._ambiguous_capacity_reservations == {"sandbox-1"}

    inventory = asyncio.run(provider.list_managed())
    assert [item.ref.sandbox_id for item in inventory] == ["sandbox-1"]
    assert provider._capacity_reservations == set()
    assert provider._ambiguous_capacity_reservations == set()
    assert provider._observed_active_count == 1

    assert asyncio.run(provider.release("sandbox-1")) is True
    assert asyncio.run(provider.create("sandbox-2", SandboxEnsureRequest())).ready


def test_daytona_inventory_reconciles_ambiguous_local_reservation() -> None:
    provider, client = _daytona_provider(
        max_active=1,
        admission_wait_seconds=0.01,
    )
    client.fail_lookup_after_accept = True

    with pytest.raises(ProviderError) as caught:
        asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert caught.value.code == "provider_create_outcome_unknown"
    assert provider._capacity_reservations == {"sandbox-1"}
    assert provider._ambiguous_capacity_reservations == {"sandbox-1"}

    inventory = asyncio.run(provider.list_managed())
    assert [item.ref.sandbox_id for item in inventory] == ["sandbox-1"]
    assert provider._capacity_reservations == set()
    assert provider._ambiguous_capacity_reservations == set()
    assert provider._observed_active_count == 1

    assert asyncio.run(provider.release("sandbox-1")) is True
    assert asyncio.run(provider.create("sandbox-2", SandboxEnsureRequest())).ready


def test_e2b_empty_inventory_does_not_authorize_replacement() -> None:
    provider = _e2b_provider_with()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert asyncio.run(provider.get_status("sandbox-1")).ready
    provider_id = provider._known_infos["sandbox-1"].provider_id

    _E2BSandbox.instances.pop(provider_id)
    _E2BSandbox.infos.pop(provider_id)
    inventory = asyncio.run(provider.list_managed())
    assert [item.ref.provider_id for item in inventory] == [provider_id]
    assert provider._known_infos["sandbox-1"].provider_id == provider_id
    assert provider._sandboxes["sandbox-1"].sandbox_id == provider_id

    with pytest.raises(ProviderError) as unavailable:
        asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert unavailable.value.code == "provider_adoption_unavailable"
    assert _E2BSandbox.create_count == 1


def test_e2b_not_found_status_preserves_identity_and_fails_closed() -> None:
    provider = _e2b_provider_with()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = provider._known_infos["sandbox-1"].provider_id
    _E2BSandbox.instances.pop(provider_id)
    _E2BSandbox.infos.pop(provider_id)

    with pytest.raises(ProviderError) as status_error:
        asyncio.run(provider.get_status("sandbox-1"))
    assert status_error.value.code == "provider_adoption_unavailable"
    assert status_error.value.status_code == 503
    assert provider._known_infos["sandbox-1"].provider_id == provider_id
    assert "sandbox-1" not in provider._sandboxes
    with pytest.raises(ProviderError) as unavailable:
        asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert unavailable.value.code == "provider_adoption_unavailable"
    assert _E2BSandbox.create_count == 1


def test_e2b_partial_inventory_does_not_prune_cache() -> None:
    provider = _e2b_provider_with()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = provider._known_infos["sandbox-1"].provider_id
    _E2BSandbox.instances.pop(provider_id)
    _E2BSandbox.infos.pop(provider_id)
    _E2BSandbox.fail_partial_next_list = True

    with pytest.raises(_ProviderFailure):
        asyncio.run(provider.list_managed())
    assert provider._known_infos["sandbox-1"].provider_id == provider_id


def test_daytona_complete_inventory_prunes_out_of_band_deleted_cache() -> None:
    provider, client = _daytona_provider()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert asyncio.run(provider.get_status("sandbox-1")).ready
    client.sandboxes.clear()

    assert asyncio.run(provider.list_managed()) == []
    assert "sandbox-1" not in provider._sandboxes
    assert asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest())).ready
    assert client.create_count == 2


def test_daytona_status_revalidates_cache_and_ensure_recreates() -> None:
    provider, client = _daytona_provider()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    client.sandboxes.clear()

    with pytest.raises(SandboxNotFoundError):
        asyncio.run(provider.get_status("sandbox-1"))
    assert "sandbox-1" not in provider._sandboxes
    assert asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest())).ready
    assert client.create_count == 2


def test_daytona_partial_inventory_does_not_prune_cache() -> None:
    provider, client = _daytona_provider()
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    cached = provider._sandboxes["sandbox-1"]
    client.sandboxes.clear()
    client.fail_partial_next_list = True

    with pytest.raises(_ProviderFailure):
        asyncio.run(provider.list_managed())
    assert provider._sandboxes["sandbox-1"] is cached


def test_e2b_not_found_during_resume_preserves_identity_and_fails_closed() -> None:
    provider = _e2b_provider_with(max_active=1)
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    provider_id = provider._known_infos["sandbox-1"].provider_id
    assert asyncio.run(provider.release("sandbox-1")) is True
    _E2BSandbox.instances.pop(provider_id)
    _E2BSandbox.infos.pop(provider_id)

    with pytest.raises(ProviderError) as unavailable:
        asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert unavailable.value.code == "provider_adoption_unavailable"
    assert _E2BSandbox.create_count == 1


def test_daytona_not_found_during_resume_recreates_provider_object() -> None:
    provider, client = _daytona_provider(max_active=1)
    asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    assert asyncio.run(provider.release("sandbox-1")) is True
    client.fail_next_start_not_found = True

    assert asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest())).ready
    assert client.create_count == 2
