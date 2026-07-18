from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta, timezone
from io import BytesIO
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from fastapi import UploadFile
from opentelemetry import trace
from opentelemetry.trace import NonRecordingSpan, SpanContext, TraceFlags, TraceState
from pydantic import BaseModel, ValidationError

from app.app import RequestBodyLimitMiddleware
from app.core.api.uploads import (
    UPLOAD_MEMORY_SPOOL_BYTES,
    UploadBudget,
    read_upload_limited,
    stage_upload_limited,
    upload_source_has_content,
    upload_source_sha256,
    upload_source_size,
)
from app.core.domain.errors import DomainError, PayloadTooLargeError
from app.core.domain.events import DomainEvent
from app.core.infrastructure.db.uow import SqlAlchemyUnitOfWork
from app.core.infrastructure.events.inbox import (
    InboxConsumer,
    InboxStatus,
    provide_domain_event_inbox,
    stable_event_id,
)
from app.core.infrastructure.events.outbox import (
    ClaimedEvent,
    OutboxDispatcher,
    outbox_dispatcher_lifespan,
    replay_outbox_event,
)
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
        self.committed = False
        self.rolled_back = False

    async def execute(self, statement):
        self.statements.append(statement)

    async def commit(self) -> None:
        self.committed = True

    async def rollback(self) -> None:
        self.rolled_back = True


@pytest.mark.asyncio
async def test_uow_stages_event_before_database_commit() -> None:
    session = _FakeSession()
    uow = SqlAlchemyUnitOfWork(session)  # type: ignore[arg-type]
    event = _TestEvent(value="committed")
    second_event = _TestEvent(value="also-committed")
    uow.collect_events([event, second_event])

    await uow.commit()

    assert session.committed is True
    assert len(session.statements) == 1
    params = session.statements[0].compile().params
    assert params["event_type_m0"] == "test.created"
    assert params["stream_m0"] == "test_events"
    assert params["id_m0"] == event.event_id
    assert params["payload_m0"]["value"] == "committed"
    assert params["id_m1"] == second_event.event_id
    assert params["payload_m1"]["value"] == "also-committed"
    assert not uow.has_pending_events()


class _MemoryInbox(InboxConsumer):
    def __init__(self, attempt: int = 1) -> None:
        super().__init__(AsyncMock(), max_attempts=10)  # type: ignore[arg-type]
        self.attempt = attempt
        self.finished: list[tuple[InboxStatus, str | None]] = []

    async def _claim(self, consumer, event_id, event_type):
        return self.attempt

    async def _finish(
        self,
        consumer,
        event_id,
        status,
        *,
        error_type: str | None = None,
        failed_attempts: int | None = None,
    ) -> None:
        del failed_attempts
        self.finished.append((status, error_type))


class _AsyncContext:
    def __init__(self, value) -> None:
        self.value = value

    async def __aenter__(self):
        return self.value

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        return None


class _DatabaseSessionDouble:
    def __init__(self, *, row=None, rows=(), result=None) -> None:
        self.row = row
        self.rows = list(rows)
        self.result = result or SimpleNamespace(rowcount=1)
        self.statements: list[object] = []

    def begin(self):
        return _AsyncContext(self)

    async def execute(self, statement):
        self.statements.append(statement)
        return self.result

    async def scalar(self, statement):
        self.statements.append(statement)
        return self.row

    async def scalars(self, statement):
        self.statements.append(statement)
        return SimpleNamespace(all=lambda: self.rows)


def _session_maker(session: _DatabaseSessionDouble):
    return lambda: _AsyncContext(session)


@pytest.mark.asyncio
async def test_inbox_classifies_success_retry_terminal_and_dead_letter() -> None:
    event = _TestEvent(value="one")

    success = _MemoryInbox()
    await success.process("consumer", event, AsyncMock(return_value=None))
    assert success.finished == [(InboxStatus.COMPLETED, None)]

    retry = _MemoryInbox()

    async def infrastructure_failure() -> None:
        raise RuntimeError("database unavailable")

    with pytest.raises(RuntimeError):
        await retry.process("consumer", event, infrastructure_failure)
    assert retry.finished == [(InboxStatus.RETRYING, "RuntimeError")]

    terminal = _MemoryInbox()

    async def validation_failure() -> None:
        raise DomainError("invalid", status_code=422)

    await terminal.process("consumer", event, validation_failure)
    assert terminal.finished == [(InboxStatus.TERMINAL, "DomainError")]

    dead_letter = _MemoryInbox(attempt=10)
    await dead_letter.process("consumer", event, infrastructure_failure)
    assert dead_letter.finished == [(InboxStatus.DEAD_LETTER, "RuntimeError")]


@pytest.mark.asyncio
async def test_inbox_propagates_event_lineage_to_resulting_events() -> None:
    parent = _TestEvent(value="parent")
    child: _TestEvent | None = None

    async def create_child() -> None:
        nonlocal child
        child = _TestEvent(value="child")

    await _MemoryInbox().process("consumer", parent, create_child)

    assert parent.correlation_id == parent.event_id
    assert parent.causation_id is None
    assert child is not None
    assert child.correlation_id == parent.correlation_id
    assert child.causation_id == parent.event_id
    unrelated = _TestEvent(value="unrelated")
    assert unrelated.correlation_id == unrelated.event_id
    assert unrelated.causation_id is None


@pytest.mark.asyncio
async def test_domain_event_propagates_w3c_trace_context_through_inbox() -> None:
    trace_id = int("1234567890abcdef1234567890abcdef", 16)
    span_id = int("1234567890abcdef", 16)
    span_context = SpanContext(
        trace_id=trace_id,
        span_id=span_id,
        is_remote=False,
        trace_flags=TraceFlags.SAMPLED,
        trace_state=TraceState(),
    )

    with trace.use_span(NonRecordingSpan(span_context)):
        event = _TestEvent(value="trace-context")

    assert event.traceparent == (
        "00-1234567890abcdef1234567890abcdef-1234567890abcdef-01"
    )
    assert event.model_dump(mode="json")["traceparent"] == event.traceparent

    observed_trace_id: int | None = None

    async def observe_context() -> None:
        nonlocal observed_trace_id
        observed_trace_id = trace.get_current_span().get_span_context().trace_id

    await _MemoryInbox().process("consumer", event, observe_context)

    assert observed_trace_id == trace_id


def test_domain_event_drops_invalid_or_absent_w3c_trace_context() -> None:
    invalid = _TestEvent(value="invalid", traceparent="not-a-traceparent")
    root = _TestEvent(value="root")

    assert invalid.traceparent is None
    assert invalid.tracestate is None
    assert "traceparent" not in invalid.model_dump(mode="json")
    assert "tracestate" not in invalid.model_dump(mode="json")
    assert "traceparent" not in root.model_dump(mode="json")
    assert "tracestate" not in root.model_dump(mode="json")


class _ValidationProbe(BaseModel):
    value: int


@pytest.mark.asyncio
async def test_inbox_covers_skip_cancellation_validation_and_retryable_domain_error() -> (
    None
):
    event = _TestEvent(value="branches")

    skipped = _MemoryInbox(attempt=None)  # type: ignore[arg-type]
    assert await skipped.process("consumer", event, AsyncMock()) is False

    cancelled = _MemoryInbox()

    async def cancel() -> None:
        raise asyncio.CancelledError

    with pytest.raises(asyncio.CancelledError):
        await cancelled.process("consumer", event, cancel)
    assert cancelled.finished == []

    with pytest.raises(ValidationError) as exc_info:
        _ValidationProbe.model_validate({"value": "not-an-integer"})

    async def invalid() -> None:
        raise exc_info.value

    terminal = _MemoryInbox()
    assert await terminal.process("consumer", event, invalid) is True
    assert terminal.finished == [(InboxStatus.TERMINAL, "ValidationError")]

    retryable = _MemoryInbox()

    async def dependency_failure() -> None:
        raise DomainError("dependency unavailable", status_code=503)

    with pytest.raises(DomainError):
        await retryable.process("consumer", event, dependency_failure)
    assert retryable.finished == [(InboxStatus.RETRYING, "DomainError")]


@pytest.mark.asyncio
async def test_inbox_claim_and_finish_persist_all_state_transitions() -> None:
    now = datetime.now(timezone.utc)
    event_id = uuid4()
    claimable = SimpleNamespace(
        status=InboxStatus.RETRYING.value,
        attempts=2,
        delivery_count=1,
        last_received_at=now - timedelta(minutes=2),
        last_error_type="OldError",
        last_error="old",
    )
    session = _DatabaseSessionDouble(row=claimable)
    inbox = InboxConsumer(_session_maker(session), abandon_after_seconds=60)

    assert await inbox._claim("worker", event_id, "test.created") == 3
    assert claimable.status == InboxStatus.PROCESSING.value
    assert claimable.attempts == 2
    assert claimable.delivery_count == 2
    assert claimable.last_error_type is None
    assert claimable.last_error is None

    for row in (
        None,
        SimpleNamespace(
            status=InboxStatus.COMPLETED.value,
            attempts=1,
            delivery_count=1,
            last_received_at=now,
        ),
        SimpleNamespace(
            status=InboxStatus.PROCESSING.value,
            attempts=1,
            delivery_count=1,
            last_received_at=now,
        ),
    ):
        candidate = InboxConsumer(_session_maker(_DatabaseSessionDouble(row=row)))
        assert await candidate._claim("worker", event_id, "test.created") is None

    abandoned = SimpleNamespace(
        status=InboxStatus.PROCESSING.value,
        attempts=4,
        delivery_count=3,
        last_received_at=now - timedelta(minutes=2),
        last_error_type="WorkerLost",
        last_error="abandoned",
    )
    reclaiming = InboxConsumer(_session_maker(_DatabaseSessionDouble(row=abandoned)))
    assert await reclaiming._claim("worker", event_id, "test.created") == 5
    assert abandoned.attempts == 4
    assert abandoned.delivery_count == 4

    finish_row = SimpleNamespace(
        status=None,
        last_received_at=None,
        completed_at=None,
        dead_lettered_at=None,
        last_error_type=None,
        last_error=None,
        attempts=0,
    )
    finisher = InboxConsumer(_session_maker(_DatabaseSessionDouble(row=finish_row)))
    await finisher._finish(
        "worker",
        event_id,
        InboxStatus.DEAD_LETTER,
        error_type="X" * 300,
    )
    assert finish_row.dead_lettered_at is not None
    assert finish_row.completed_at is None
    assert len(finish_row.last_error_type) == 200
    assert "trace" in finish_row.last_error

    await finisher._finish("worker", event_id, InboxStatus.COMPLETED)
    assert finish_row.completed_at is not None
    assert finish_row.dead_lettered_at is None
    assert finish_row.last_error is None

    missing = InboxConsumer(_session_maker(_DatabaseSessionDouble(row=None)))
    await missing._finish("worker", event_id, InboxStatus.COMPLETED)
    assert provide_domain_event_inbox() is not None


def test_legacy_event_id_is_deterministic() -> None:
    payload = {"event_type": "legacy", "record_id": "42", "payload": {"x": 1}}
    assert stable_event_id(payload) == stable_event_id(dict(payload))
    assert stable_event_id(payload) != stable_event_id({**payload, "record_id": "43"})


class _OutboxUnderTest(OutboxDispatcher):
    def __init__(self, bus) -> None:
        super().__init__(AsyncMock(), bus)  # type: ignore[arg-type]
        self.published: list[object] = []
        self.failed: list[tuple[object, str]] = []

    async def _mark_published(self, event_id) -> None:
        self.published.append(event_id)

    async def _mark_failed(self, event, exc) -> None:
        self.failed.append((event, type(exc).__name__))


@pytest.mark.asyncio
async def test_outbox_dispatcher_persists_publish_outcome() -> None:
    event = ClaimedEvent(
        id=uuid4(),
        stream="test_events",
        event_type="test.created",
        payload={"event_type": "test.created"},
        attempts=1,
        occurred_at=_TestEvent(value="x").occurred_at,
    )
    bus = AsyncMock()
    dispatcher = _OutboxUnderTest(bus)
    await dispatcher._publish(event)
    assert dispatcher.published == [event.id]
    assert dispatcher.failed == []

    bus.publish.side_effect = RuntimeError("redis down")
    await dispatcher._publish(event)
    assert dispatcher.failed == [(event, "RuntimeError")]


@pytest.mark.asyncio
async def test_outbox_run_recovers_from_infrastructure_failure(monkeypatch) -> None:
    dispatcher = _OutboxUnderTest(AsyncMock())
    calls = 0

    async def dispatch_once() -> int:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError("database migration not ready")
        raise asyncio.CancelledError

    dispatcher.dispatch_once = dispatch_once  # type: ignore[method-assign]
    sleep = AsyncMock()
    monkeypatch.setattr("app.core.infrastructure.events.outbox.asyncio.sleep", sleep)

    with pytest.raises(asyncio.CancelledError):
        await dispatcher.run()

    assert calls == 2
    sleep.assert_awaited_once()


@pytest.mark.asyncio
async def test_outbox_claim_state_updates_replay_and_lifespan(monkeypatch) -> None:
    now = datetime.now(timezone.utc)
    row = SimpleNamespace(
        id=uuid4(),
        stream="test_events",
        event_type="test.created",
        payload={"value": "one"},
        attempts=0,
        occurred_at=now,
        lease_owner=None,
        lease_until=None,
    )
    session = _DatabaseSessionDouble(rows=[row])
    dispatcher = OutboxDispatcher(
        _session_maker(session), AsyncMock(), owner="test-owner", lease_seconds=30
    )

    claimed = await dispatcher._claim_batch()
    assert claimed == [
        ClaimedEvent(
            id=row.id,
            stream="test_events",
            event_type="test.created",
            payload={"value": "one"},
            attempts=0,
            occurred_at=now,
        )
    ]
    assert row.lease_owner == "test-owner"
    assert row.lease_until is not None

    await dispatcher._mark_published(row.id)
    await dispatcher._mark_failed(claimed[0], RuntimeError("redis unavailable"))
    terminal = ClaimedEvent(
        id=row.id,
        stream=row.stream,
        event_type=row.event_type,
        payload=row.payload,
        attempts=9,
        occurred_at=row.occurred_at,
    )
    await dispatcher._mark_failed(terminal, RuntimeError("redis unavailable"))
    assert len(session.statements) == 4

    replay_session = _DatabaseSessionDouble(result=SimpleNamespace(rowcount=1))
    assert await replay_outbox_event(_session_maker(replay_session), row.id) is True

    started = asyncio.Event()

    async def running(self) -> None:
        started.set()
        await asyncio.Event().wait()

    monkeypatch.setattr(OutboxDispatcher, "run", running)
    async with outbox_dispatcher_lifespan(_session_maker(session), AsyncMock()):
        await started.wait()


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
