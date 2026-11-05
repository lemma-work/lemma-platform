from __future__ import annotations

import json
from io import BytesIO
from unittest.mock import AsyncMock

import pytest
from fastapi import UploadFile

from app.middleware import RequestBodyLimitMiddleware
from app.core.api.uploads import (
    UPLOAD_MEMORY_SPOOL_BYTES,
    UploadBudget,
    read_upload_limited,
    stage_upload_limited,
    upload_source_has_content,
    upload_source_sha256,
    upload_source_size,
)
from app.core.domain.errors import PayloadTooLargeError
from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.redaction import (
    REDACTED,
    REDACTED_URL,
    _redact_url,
    redact_text,
    redact_value,
)


class _TestEvent(DomainEvent):
    event_type: str = "test.created"
    value: str

    @classmethod
    def stream_name(cls) -> str:
        return "test_events"


class _FakeSession:
    def __init__(self) -> None:
        self.statements: list[object] = []
        self.parameters: list[object] = []
        self.committed = False
        self.rolled_back = False
        self.info: dict = {}

    async def execute(self, statement, parameters=None):
        self.statements.append(statement)
        self.parameters.append(parameters)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.asyncio
async def test_a_commit_writes_no_outbox_and_offers_its_events_after(
    monkeypatch,
) -> None:
    """The unit of work stages nothing: committed events go to the sink only."""
    from app.core.infrastructure.db import uow as uow_module

    offered: list[list[DomainEvent]] = []
    monkeypatch.setattr(
        uow_module, "offer_committed", lambda events: offered.append(list(events))
    )
    session = _FakeSession()
    uow = SqlAlchemyUnitOfWork(session)  # type: ignore[arg-type]
    event = _TestEvent(value="x")
    uow.collect_events([event])
    await uow.commit()
    assert session.statements == []
    assert session.committed
    assert offered == [[event]]

    rolled_back = SqlAlchemyUnitOfWork(_FakeSession())  # type: ignore[arg-type]
    rolled_back.collect_events([_TestEvent(value="y")])
    await rolled_back.rollback()
    await rolled_back.commit()
    assert offered == [[event], []]


def test_domain_event_drops_invalid_or_absent_w3c_trace_context() -> None:
    invalid = _TestEvent(value="invalid", traceparent="not-a-traceparent")
    root = _TestEvent(value="root")

    assert invalid.traceparent is None
    assert invalid.tracestate is None
    assert "traceparent" not in invalid.model_dump(mode="json")
    assert "tracestate" not in invalid.model_dump(mode="json")
    assert "traceparent" not in root.model_dump(mode="json")
    assert "tracestate" not in root.model_dump(mode="json")


@pytest.mark.asyncio
async def test_upload_reader_enforces_field_and_batch_limits() -> None:
    upload = UploadFile(filename="sample.bin", file=BytesIO(b"abcdef"))
    result = await read_upload_limited(upload, max_bytes=6, field="file")
    assert result.data == b"abcdef"
    assert result.size == 6
    assert len(result.sha256) == 64

    oversized = UploadFile(filename="large.bin", file=BytesIO(b"abcdefg"))
    with pytest.raises(PayloadTooLargeError):
        await read_upload_limited(oversized, max_bytes=6, field="file")

    budget = UploadBudget(max_bytes=5, field="batch")
    with pytest.raises(PayloadTooLargeError):
        budget.consume(6)


@pytest.mark.asyncio
async def test_upload_staging_spills_and_always_cleans_up(
    tmp_path, monkeypatch
) -> None:
    staged_path = tmp_path / "upload.staged"
    monkeypatch.setattr("app.core.api.uploads._new_staging_path", lambda: staged_path)
    content = b"x" * (UPLOAD_MEMORY_SPOOL_BYTES + 1)
    upload = UploadFile(filename="large.bin", file=BytesIO(content))

    with pytest.raises(RuntimeError, match="storage failed"):
        async with stage_upload_limited(
            upload,
            max_bytes=len(content),
            field="file",
        ) as staged:
            assert staged.path == staged_path
            assert staged.path.exists()
            assert staged.path.stat().st_size == len(content)
            assert await staged.read_bytes() == content
            raise RuntimeError("storage failed")

    assert not staged_path.exists()


@pytest.mark.asyncio
async def test_upload_helpers_cover_empty_path_and_append_spooling(
    tmp_path, monkeypatch
) -> None:
    blank = tmp_path / "blank.bin"
    blank.write_bytes(b" \n\t")
    assert upload_source_size(blank) == 3
    assert upload_source_has_content(blank) is False
    assert upload_source_has_content(b" \t") is False

    content_path = tmp_path / "content.bin"
    content_path.write_bytes(b" \nvalue")
    assert upload_source_has_content(content_path) is True
    assert upload_source_sha256(content_path) == upload_source_sha256(b" \nvalue")

    empty_stage = tmp_path / "empty.staged"
    monkeypatch.setattr("app.core.api.uploads._new_staging_path", lambda: empty_stage)
    empty = UploadFile(filename="empty.bin", file=BytesIO(b""))
    async with stage_upload_limited(empty, max_bytes=0, field="file") as staged:
        assert staged.path == empty_stage
        assert staged.size == 0

    appended_stage = tmp_path / "appended.staged"
    monkeypatch.setattr(
        "app.core.api.uploads._new_staging_path", lambda: appended_stage
    )
    content = b"x" * (UPLOAD_MEMORY_SPOOL_BYTES + 2 * 1024 * 1024)
    upload = UploadFile(filename="append.bin", file=BytesIO(content))
    async with stage_upload_limited(
        upload, max_bytes=len(content), field="file"
    ) as staged:
        assert staged.path.read_bytes() == content


@pytest.mark.asyncio
async def test_request_body_limit_rejects_chunked_body_without_content_length() -> None:
    async def app(scope, receive, send):
        while (await receive()).get("more_body"):
            pass
        await send({"type": "http.response.start", "status": 204, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    middleware = RequestBodyLimitMiddleware(app, max_bytes=5)
    chunks = iter(
        [
            {"type": "http.request", "body": b"abc", "more_body": True},
            {"type": "http.request", "body": b"def", "more_body": False},
        ]
    )
    sent: list[dict] = []

    async def receive():
        return next(chunks)

    async def send(message):
        sent.append(message)

    await middleware(
        {"type": "http", "method": "POST", "path": "/upload", "headers": []},
        receive,
        send,
    )
    assert sent[0]["status"] == 413
    body = json.loads(sent[1]["body"])
    assert body["code"] == "UPLOAD_TOO_LARGE"


@pytest.mark.asyncio
async def test_request_body_limit_handles_header_fast_path_and_passthrough() -> None:
    app = AsyncMock()
    receive = AsyncMock()
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    middleware = RequestBodyLimitMiddleware(app, max_bytes=5)
    await middleware(
        {
            "type": "http",
            "method": "POST",
            "path": "/upload",
            "headers": [(b"content-length", b"6"), (b"x-request-id", b"req-1")],
        },
        receive,
        send,
    )
    assert sent[0]["status"] == 413
    assert json.loads(sent[1]["body"])["request_id"] == "req-1"
    app.assert_not_awaited()

    async def valid_app(scope, receive, send):
        del scope, receive, send

    valid = RequestBodyLimitMiddleware(valid_app, max_bytes=5)
    await valid(
        {
            "type": "http",
            "headers": [(b"content-length", b"invalid")],
        },
        AsyncMock(),
        AsyncMock(),
    )

    disabled_app = AsyncMock()
    disabled = RequestBodyLimitMiddleware(disabled_app, max_bytes=0)
    scope = {"type": "websocket", "headers": []}
    await disabled(scope, receive, send)
    disabled_app.assert_awaited_once_with(scope, receive, send)


def test_canary_secrets_are_redacted_recursively_and_in_text() -> None:
    canary = "CANARY-SECRET-123"
    value = {
        "authorization": f"Bearer {canary}",
        "provider": {
            "refresh_token": canary,
            "callback": f"https://provider.test/cb?code={canary}&state={canary}",
        },
    }
    rendered = json.dumps(redact_value(value))
    assert canary not in rendered
    assert REDACTED in rendered
    assert canary not in redact_text(f"provider failed api_key={canary}")
    assert canary not in redact_text(
        f"GET https://provider.test/cb?code={canary} failed"
    )
    assert canary not in redact_text(f'provider response {{"token": "{canary}"}}')


@pytest.mark.parametrize(
    "credential_parts",
    [
        ("AKIA", "IOSFODNN7EXAMPLE"),
        ("AIza", "SyD-example-credential-123456"),
        ("ghp", "_1234567890abcdefghijklmnopqrst"),
        ("sk-proj", "-1234567890abcdefghijklmnop"),
        ("sk", "_live_1234567890abcdefghijklmnop"),
        ("xoxb", "-1234567890-abcdefghijklmnop"),
        ("123456789", ":AA1234567890abcdefghijklmnop"),
    ],
)
def test_known_bare_credential_patterns_are_redacted(
    credential_parts: tuple[str, str],
) -> None:
    # Assemble canaries at runtime so secret-scanning push protection never
    # mistakes a test fixture for a committed live credential.
    credential = "".join(credential_parts)
    rendered = redact_text(f"provider returned credential {credential}")
    assert credential not in rendered
    assert REDACTED in rendered


def test_private_keys_and_sensitive_url_fragments_are_redacted() -> None:
    private_key = (
        "-----BEGIN "
        "PRIVATE KEY-----\n"
        "CANARY-PRIVATE-MATERIAL\n"
        "-----END "
        "PRIVATE KEY-----"
    )
    assert "CANARY" not in redact_text(f"provider returned {private_key}")
    assert (
        _redact_url(
            "https://provider.test/callback?ok=yes"
            "#access_token=CANARY&token_type=bearer"
        )
        == "https://provider.test/callback?ok=yes#[REDACTED]"
    )


def test_redaction_handles_urls_exceptions_sequences_and_binary_values() -> None:
    rendered = redact_value(
        [
            RuntimeError("provider secret"),
            b"binary-secret",
            "Bearer token-value",
            {"ordinary": "eyJabc.def.ghi"},
        ]
    )
    assert rendered[0] == {"type": "RuntimeError"}
    assert rendered[1] == "<bytes:13>"
    assert REDACTED in rendered[2]
    assert REDACTED in rendered[3]["ordinary"]
    assert (
        _redact_url("https://user:password@example.test:8443/cb?state=secret&ok=yes")
        == "https://[REDACTED]@example.test:8443/cb?state=%5BREDACTED%5D&ok=yes"
    )
    assert _redact_url("not a url") == "not a url"
    assert redact_value(7) == 7


@pytest.mark.parametrize(
    "value",
    [
        "https://example.com:bad/path?token=CANARY",
        "https://[broken/path?token=CANARY",
        "https://user:secret@example.com:999999/path?code=CANARY",
    ],
)
def test_redaction_of_malformed_urls_is_bounded_and_non_throwing(value: str) -> None:
    assert _redact_url(value) == REDACTED_URL
    rendered = redact_text(f"dependency failed at {value}")
    assert REDACTED_URL in rendered
    assert "CANARY" not in rendered


async def test_after_commit_callbacks_run_only_once_the_commit_has_happened():
    """Deferred work must not observe, or be observed by, an open transaction.

    Cache invalidation is the reason this exists. Running it inline holds the
    connection across a Redis round trip, and it is the wrong order besides: a
    concurrent reader can repopulate the cache from the pre-commit state in the
    window between invalidating and committing.
    """
    from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork

    order: list[str] = []

    class _Session:
        info: dict = {}

        async def commit(self) -> None:
            order.append("commit")

        async def execute(self, *args, **kwargs):
            return None

    uow = SqlAlchemyUnitOfWork(_Session())

    async def _invalidate() -> None:
        order.append("invalidate")

    uow.after_commit(_invalidate)
    assert order == [], "the callback ran before the commit"

    await uow.commit()
    assert order == ["commit", "invalidate"]

    # And it does not run again on a second commit.
    await uow.commit()
    assert order == ["commit", "invalidate", "commit"]
