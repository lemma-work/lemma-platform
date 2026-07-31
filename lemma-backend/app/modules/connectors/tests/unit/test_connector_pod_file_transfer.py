"""Pod datastore file transfer for OpenAPI connector operations (upload/download)."""

from __future__ import annotations

import base64
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.connectors.domain.errors import OperationExecutionValidationError
from app.modules.connectors.services.connector_operation_service import (
    ConnectorOperationService,
)


class _FakeGateway:
    def __init__(self):
        self.reads = []
        self.writes = []

    async def read_bytes(self, *, pod_id, path, ctx):
        self.reads.append(path)
        return b"filedata", "application/octet-stream", "a.bin"

    async def write_bytes(self, *, pod_id, directory, name, content, media_type, ctx):
        self.writes.append({"directory": directory, "name": name, "content": content})
        return {
            "type": "pod_file",
            "pod_path": f"{directory.rstrip('/')}/{name}",
            "size_bytes": len(content),
            "media_type": media_type,
        }


def _service(gateway):
    return ConnectorOperationService(
        connector_repository=None,
        operation_repository=None,
        operation_gateway=None,
        account_resolution_service=None,
        pod_file_gateway=gateway,
    )


def _op(execution):
    return SimpleNamespace(execution=execution)


def _ctx():
    return SimpleNamespace(pod_id=uuid4())


@pytest.mark.asyncio
async def test_octet_stream_body_pod_path_resolved_to_bytes():
    gateway = _FakeGateway()
    svc = _service(gateway)
    op = _op({
        "mode": "openapi",
        "request_body": {"field": "body", "binary_fields": ["body"]},
    })
    payload = {"owner": "o", "body": {"pod_path": "/me/a.bin"}}

    out = await svc._resolve_pod_file_inputs(op, payload, _ctx())

    assert gateway.reads == ["/me/a.bin"]
    assert out["body"] == {"bytes": b"filedata", "filename": "a.bin"}
    assert out["owner"] == "o"


@pytest.mark.asyncio
async def test_multipart_field_pod_path_resolved():
    gateway = _FakeGateway()
    svc = _service(gateway)
    op = _op({
        "mode": "openapi",
        "request_body": {"field": "body", "binary_fields": ["file"], "form_fields": ["name"]},
    })
    payload = {"body": {"file": {"pod_path": "/me/a.bin"}, "name": "keep"}}

    out = await svc._resolve_pod_file_inputs(op, payload, _ctx())

    assert out["body"]["file"] == {"bytes": b"filedata", "filename": "a.bin"}
    assert out["body"]["name"] == "keep"  # non-file field untouched


@pytest.mark.asyncio
async def test_base64_input_is_left_untouched():
    gateway = _FakeGateway()
    svc = _service(gateway)
    op = _op({"mode": "openapi", "request_body": {"field": "body", "binary_fields": ["body"]}})
    payload = {"body": {"base64": "aGk="}}

    out = await svc._resolve_pod_file_inputs(op, payload, _ctx())

    assert gateway.reads == []  # no datastore call
    assert out["body"] == {"base64": "aGk="}


@pytest.mark.asyncio
async def test_pod_path_without_pod_context_raises():
    svc = _service(_FakeGateway())
    op = _op({"mode": "openapi", "request_body": {"field": "body", "binary_fields": ["body"]}})
    payload = {"body": {"pod_path": "/me/a.bin"}}

    with pytest.raises(OperationExecutionValidationError):
        await svc._resolve_pod_file_inputs(op, payload, actor=None)  # no pod_id


@pytest.mark.asyncio
async def test_no_binary_fields_is_noop():
    gateway = _FakeGateway()
    svc = _service(gateway)
    op = _op({"mode": "openapi", "request_body": {"field": "body", "binary_fields": []}})
    payload = {"body": {"title": "hi"}}
    out = await svc._resolve_pod_file_inputs(op, payload, _ctx())
    assert out == payload
    assert gateway.reads == []


@pytest.mark.asyncio
async def test_write_binary_output_to_datastore():
    gateway = _FakeGateway()
    svc = _service(gateway)
    result = {
        "type": "binary_content",
        "content_base64": base64.b64encode(b"tarball").decode(),
        "media_type": "application/gzip",
        "file_name": "x.tgz",
    }
    ref = await svc.write_binary_output(result, output_path="/me/out.tgz", actor=_ctx())

    assert ref["type"] == "pod_file"
    assert ref["pod_path"] == "/me/out.tgz"
    assert gateway.writes[0]["content"] == b"tarball"
    assert gateway.writes[0]["name"] == "out.tgz"
    assert gateway.writes[0]["directory"] == "/me"


@pytest.mark.asyncio
async def test_write_binary_output_noop_without_output_path():
    gateway = _FakeGateway()
    svc = _service(gateway)
    result = {"type": "binary_content", "content_base64": "", "media_type": "application/gzip"}
    out = await svc.write_binary_output(result, output_path=None, actor=_ctx())
    assert out is result
    assert gateway.writes == []


@pytest.mark.asyncio
async def test_write_binary_output_noop_for_non_datastore_path():
    gateway = _FakeGateway()
    svc = _service(gateway)
    result = {"type": "binary_content", "content_base64": "", "media_type": "application/gzip"}
    out = await svc.write_binary_output(result, output_path="/workspace/out.tgz", actor=_ctx())
    assert out is result
    assert gateway.writes == []
