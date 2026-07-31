"""Worker retry classification for GitHub provider failures."""

from types import SimpleNamespace
from uuid import uuid4

import pytest
from streaq import StreaqRetry

from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionUnauthorizedError,
)
from app.modules.pod_bundle.domain.errors import (
    GithubImportError,
    GithubPublishCapabilityUnavailableError,
)
from app.modules.pod_bundle.domain.state import (
    BundleSource,
    ImportState,
    PublishState,
)
from app.modules.pod_bundle.events import handlers, publish_task
from app.modules.pod_bundle.events.handlers import _is_retryable_import_error
from app.modules.pod_bundle.events.publish_task import (
    _is_retryable_publish_error,
)


def test_publish_retries_provider_outage_but_not_auth_or_missing_capability():
    assert _is_retryable_publish_error(
        OperationExecutionInfrastructureError("provider down")
    )
    assert not _is_retryable_publish_error(
        OperationExecutionUnauthorizedError("expired")
    )
    assert not _is_retryable_publish_error(
        GithubPublishCapabilityUnavailableError("GITHUB_CREATE_A_BLOB")
    )


def test_import_retries_rate_limit_and_provider_outage_but_not_auth():
    assert _is_retryable_import_error(
        GithubImportError(
            "rate limited",
            code="GITHUB_RATE_LIMITED",
            status_code=429,
        )
    )
    assert _is_retryable_import_error(
        GithubImportError(
            "provider down",
            code="GITHUB_IMPORT_TRANSIENT",
            status_code=503,
        )
    )
    assert not _is_retryable_import_error(
        GithubImportError(
            "denied",
            code="GITHUB_IMPORT_UNAUTHORIZED",
            status_code=403,
        )
    )


class _StateStore:
    def __init__(self, state):
        self.state = state

    async def get_import(self, _job_id):
        return self.state

    async def save_import(self, state):
        self.state = state

    async def get_publish(self, _job_id):
        return self.state

    async def save_publish(self, state):
        self.state = state


async def test_github_import_schedules_bounded_streaq_retry(monkeypatch):
    state = ImportState(
        import_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        source=BundleSource(
            kind="GITHUB",
            repo_url="https://github.com/lemma-work/example",
        ),
    )
    store = _StateStore(state)

    class _Fetcher:
        async def fetch_zipball(self, **_kwargs):
            raise GithubImportError(
                "rate limited",
                code="GITHUB_RATE_LIMITED",
                status_code=429,
            )

    monkeypatch.setattr(handlers, "streaq_worker", SimpleNamespace(context=object()))
    monkeypatch.setattr(handlers, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(handlers, "BundleStagingStorage", lambda: object())
    monkeypatch.setattr(
        handlers,
        "_github_import_fetcher",
        lambda _worker_ctx, _state: _Fetcher(),
    )

    with pytest.raises(StreaqRetry) as caught:
        await handlers.import_pod_github.fn({"import_id": str(state.import_id)})

    assert caught.value.delay == 1
    assert state.retryable is True
    assert state.error_code == "GITHUB_RATE_LIMITED"


async def test_github_publish_schedules_bounded_streaq_retry(monkeypatch):
    state = PublishState(
        publish_id=uuid4(),
        pod_id=uuid4(),
        user_id=uuid4(),
        repo_name="example",
        account_id=uuid4(),
    )
    store = _StateStore(state)

    async def _provider_outage(**_kwargs):
        raise OperationExecutionInfrastructureError("provider down")

    monkeypatch.setattr(
        publish_task,
        "streaq_worker",
        SimpleNamespace(context=object()),
    )
    monkeypatch.setattr(publish_task, "get_pod_bundle_state_store", lambda: store)
    monkeypatch.setattr(publish_task, "_load_or_export_archive", _provider_outage)

    with pytest.raises(StreaqRetry) as caught:
        await publish_task.publish_pod_github.fn(
            {
                "publish_id": str(state.publish_id),
                "pod_id": str(state.pod_id),
                "user_id": str(state.user_id),
            }
        )

    assert caught.value.delay == 1
    assert state.retryable is True
    assert state.error_code == "OPERATION_EXECUTION_INFRA_ERROR"
