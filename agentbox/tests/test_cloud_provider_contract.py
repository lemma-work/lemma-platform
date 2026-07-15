from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from types import SimpleNamespace

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
from agentbox.schemas import SandboxEnsureRequest  # noqa: E402
from agentbox.providers.errors import ProviderError  # noqa: E402


class _NotFound(Exception):
    pass


class _ProviderFailure(Exception):
    pass


class _RateLimit(Exception):
    pass


class _Query:
    def __init__(self, *, metadata=None, labels=None):
        self.filters = metadata or labels or {}


class _Paginator:
    def __init__(self, items):
        self.items = list(items)
        self.has_next = True

    async def next_items(self):
        self.has_next = False
        return self.items


class _FakeCommands:
    def __init__(self) -> None:
        self.processes: list[SimpleNamespace] = []
        self.run_calls: list[dict[str, object]] = []

    async def list(self):
        return list(self.processes)

    async def run(self, cmd, **kwargs):
        self.run_calls.append({"cmd": cmd, **kwargs})
        self.processes.append(SimpleNamespace(cmd=cmd, args=[]))
        return SimpleNamespace(exit_code=None)


class _E2BSandbox:
    infos: dict[str, SimpleNamespace] = {}
    instances: dict[str, "_E2BSandbox"] = {}
    create_count = 0

    def __init__(self, sandbox_id: str) -> None:
        self.sandbox_id = sandbox_id
        self.running = True
        self.traffic_access_token = f"token-{sandbox_id}-1"
        self.timeout = 0
        self.commands = _FakeCommands()

    @classmethod
    def reset(cls) -> None:
        cls.infos = {}
        cls.instances = {}
        cls.create_count = 0

    @classmethod
    def list(cls, *, query, limit, **kwargs):
        del limit, kwargs
        items = [
            info
            for info in cls.infos.values()
            if all(info.metadata.get(key) == value for key, value in query.filters.items())
        ]
        return _Paginator(items)

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
        )
        return sandbox

    @classmethod
    async def connect(cls, provider_id, **kwargs):
        del kwargs
        try:
            return cls.instances[provider_id]
        except KeyError as exc:
            raise _NotFound from exc

    @classmethod
    async def kill(cls, provider_id, **kwargs):
        del kwargs
        if provider_id not in cls.instances:
            raise _NotFound
        cls.instances.pop(provider_id)
        cls.infos.pop(provider_id)
        return True

    async def is_running(self):
        return self.running

    async def set_timeout(self, timeout, **kwargs):
        del kwargs
        self.timeout = timeout

    def get_host(self, port):
        return f"{self.sandbox_id}-{port}.e2b.test"


def _e2b_provider() -> E2BSandboxProvider:
    _E2BSandbox.reset()
    return E2BSandboxProvider(
        E2BProviderConfig(api_key="key", template="template", max_active=2),
        sdk=_E2BSdk(
            sandbox_cls=_E2BSandbox,
            query_cls=_Query,
            rate_limit_error=_RateLimit,
            not_found_error=_NotFound,
            sandbox_error=_ProviderFailure,
        ),
    )


def _e2b_provider_with(**changes) -> E2BSandboxProvider:
    _E2BSandbox.reset()
    values = {
        "api_key": "key",
        "template": "template",
        "max_active": 2,
        "create_rate_per_second": 100,
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
        ),
    )


def test_e2b_contract_is_idempotent_and_refreshes_endpoint_token() -> None:
    provider = _e2b_provider()
    request = SandboxEnsureRequest(env={"LEMMA_BASE_URL": "https://lemma.test"})

    first = asyncio.run(provider.create("sandbox-1", request))
    second = asyncio.run(provider.create("sandbox-1", request))
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
    assert len(sandbox.commands.run_calls) == 1
    assert sandbox.commands.run_calls[0]["envs"] == {
        "LEMMA_BASE_URL": "https://lemma.test"
    }
    assert endpoint_one.headers["e2b-traffic-access-token"] != endpoint_two.headers[
        "e2b-traffic-access-token"
    ]
    assert endpoint_two.headers["e2b-traffic-access-token"] == "refreshed-token"
    assert inventory[0].ref.provider_id == endpoint_two.instance_id
    assert asyncio.run(provider.delete("sandbox-1")) is True
    assert asyncio.run(provider.delete("sandbox-1")) is False


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
        assert exc.headers == {"Retry-After": "23"}
    else:
        raise AssertionError("capacity admission unexpectedly succeeded")

    assert _E2BSandbox.create_count == 1
    assert provider._observed_active_count == 1
    assert provider._capacity_reservations == set()


def test_e2b_bootstrap_failure_deletes_new_provider_and_releases_capacity() -> None:
    provider = _e2b_provider_with(max_active=1)

    async def fail_bootstrap(sandbox, env):
        del sandbox, env
        raise RuntimeError("bootstrap failed")

    provider._bootstrap_sandbox = fail_bootstrap  # type: ignore[method-assign]
    try:
        asyncio.run(provider.create("sandbox-1", SandboxEnsureRequest()))
    except RuntimeError as exc:
        assert str(exc) == "bootstrap failed"
    else:
        raise AssertionError("bootstrap failure was not propagated")

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

    async def list(self, query):
        for sandbox in list(self.sandboxes.values()):
            if all(
                sandbox.labels.get(key) == value
                for key, value in query.filters.items()
            ):
                yield sandbox

    async def create(self, params, *, timeout):
        del timeout
        self.created_params = params
        self.create_count += 1
        sandbox = _DaytonaSandbox(
            f"daytona-{self.create_count}", params.values["labels"]
        )
        self.sandboxes[sandbox.id] = sandbox
        return sandbox

    async def start(self, sandbox, *, timeout):
        del timeout
        sandbox.state = "started"
        sandbox.updated_at = "generation-resumed"

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
        "snapshot": "snapshot",
        "max_active": 2,
        "create_rate_per_second": 100,
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
    assert endpoint_one.headers["X-Daytona-Preview-Token"] == "preview-1"
    assert endpoint_two.headers["X-Daytona-Preview-Token"] == "preview-2"
    assert endpoint_one.instance_id != endpoint_two.instance_id
    assert inventory[0].instance_id == endpoint_two.instance_id
    assert asyncio.run(provider.delete("sandbox-1")) is True
    assert asyncio.run(provider.delete("sandbox-1")) is False


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
