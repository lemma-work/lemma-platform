"""Files passed *into* an operation, on every kind.

Sending a Gmail attachment could not work. A caller passed a pod path, the
payload went to Composio as that literal dict, and Composio wanted a
`{name, mimetype, s3key}` object naming a file in its own storage -- something
no caller of ours could produce. OpenAPI uploads raised "no pod context" for
every pod path, and MCP had no file handling at all. The parsing helpers
existed; nothing read the file.
"""

from __future__ import annotations

import base64
import copy
from types import SimpleNamespace
from uuid import uuid4

import httpx
import pytest

from app.core.domain.errors import DomainError
from app.modules.connectors.domain.errors import ConnectorValidationError
from app.modules.connectors.domain.file_input import FILE_MARKER, MaterializedFile
from app.modules.connectors.infrastructure.adapters import composio_operation_gateway
from app.modules.connectors.infrastructure.adapters.mcp_executor import (
    _as_tool_arguments,
)
from app.modules.connectors.infrastructure.adapters.openapi_http_executor import (
    OpenApiHttpExecutor,
)
from app.modules.connectors.services.files.file_ref import (
    is_file_schema,
    present_input_schema,
)
from app.modules.connectors.services.files.input_resolver import (
    FileInputResolver,
    contains_file_reference,
    split_output_path,
)

pytestmark = pytest.mark.unit

_UPLOADABLE = {
    "type": "object",
    "title": "FileUploadable",
    "required": ["name", "mimetype", "s3key"],
    "properties": {
        "name": {"type": "string"},
        "mimetype": {"type": "string"},
        "s3key": {"type": "string"},
    },
    "file_uploadable": True,
}

# GMAIL_SEND_EMAIL's `attachment`, as Composio publishes it: one file or several.
GMAIL_SEND = {
    "type": "object",
    "properties": {
        "recipient_email": {"type": "string"},
        "body": {"type": "string"},
        "attachment": {"anyOf": [_UPLOADABLE, {"type": "array", "items": _UPLOADABLE}]},
    },
}

DRIVE_UPLOAD = {
    "type": "object",
    "required": ["file_to_upload"],
    "properties": {"file_to_upload": _UPLOADABLE},
}


class _Pod:
    """A pod datastore holding a couple of files, and who may read them."""

    def __init__(self, files: dict[str, tuple[bytes, str]], *, pod_id):
        self.files = files
        self.pod_id = pod_id
        self.reads: list[tuple] = []

    async def read_bytes(self, *, pod_id, path, ctx):
        self.reads.append(("path", pod_id, path, ctx))
        if path not in self.files:
            raise DomainError(
                f"File {path} not found", code="NOT_FOUND", status_code=404
            )
        content, media_type = self.files[path]
        return content, media_type, path.rsplit("/", 1)[-1]

    async def read_bytes_by_id(self, *, pod_id, file_id, ctx):
        self.reads.append(("id", pod_id, file_id, ctx))
        return b"by-id", "application/pdf", "by-id.pdf"


class TestComposioFilesAreRecognised:
    def test_file_uploadable_is_a_file(self):
        assert is_file_schema(_UPLOADABLE)

    def test_one_or_many_attachments_is_a_file_field(self):
        assert is_file_schema(GMAIL_SEND["properties"]["attachment"])

    def test_a_reference_in_an_attachment_list_is_found(self):
        payload = {"attachment": [{"pod_path": "/me/a.pdf"}, {"pod_path": "/me/b.pdf"}]}
        assert contains_file_reference(GMAIL_SEND, payload)

    def test_an_already_staged_s3key_is_not_a_reference(self):
        payload = {"attachment": {"name": "a.pdf", "mimetype": "x", "s3key": "k"}}
        assert not contains_file_reference(GMAIL_SEND, payload)

    def test_a_payload_without_files_is_left_alone(self):
        assert not contains_file_reference(GMAIL_SEND, {"body": "hi"})


class TestWhatCallersAreShown:
    def test_a_composio_file_becomes_a_reference(self):
        presented = present_input_schema(DRIVE_UPLOAD)

        node = presented["properties"]["file_to_upload"]
        assert node[FILE_MARKER] is True
        assert "pod_path" in node["properties"]
        assert "s3key" not in node["properties"]

    def test_both_attachment_shapes_become_references(self):
        presented = present_input_schema(GMAIL_SEND)

        single, many = presented["properties"]["attachment"]["anyOf"]
        assert single[FILE_MARKER] is True
        assert many["items"][FILE_MARKER] is True

    def test_the_stored_schema_is_not_touched(self):
        before = copy.deepcopy(GMAIL_SEND)
        present_input_schema(GMAIL_SEND)
        assert GMAIL_SEND == before

    def test_the_openapi_importers_own_shape_is_replaced_whole(self):
        # `oneOf [pod_path, base64, text]`: swapping only the pod_path branch
        # would let `{"base64": ...}` match two branches of a oneOf.
        importer_node = {
            "oneOf": [
                {"type": "object", "properties": {"pod_path": {"type": "string"}}},
                {"type": "object", "properties": {"base64": {"type": "string"}}},
                {"type": "string"},
            ]
        }
        presented = present_input_schema(
            {"type": "object", "properties": {"file": importer_node}}
        )
        assert presented["properties"]["file"][FILE_MARKER] is True
        assert "oneOf" not in presented["properties"]["file"]

    def test_a_reference_validates_against_what_callers_are_shown(self):
        import jsonschema

        schema = present_input_schema(GMAIL_SEND)
        jsonschema.validate({"attachment": {"pod_path": "/me/a.pdf"}}, schema)
        jsonschema.validate(
            {"attachment": [{"file_id": "x"}, {"pod_path": "/b"}]}, schema
        )
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate({"attachment": {"nonsense": 1}}, schema)


class TestReadingTheFiles:
    @pytest.mark.asyncio
    async def test_pod_paths_are_read_as_the_caller_in_their_pod(self):
        pod_id, ctx = uuid4(), object()
        pod = _Pod({"/me/q3.pdf": (b"%PDF-1.7", "application/pdf")}, pod_id=pod_id)

        resolved = await FileInputResolver(pod, pod_id=pod_id, ctx=ctx).resolve(
            GMAIL_SEND, {"body": "hi", "attachment": {"pod_path": "/me/q3.pdf"}}
        )

        assert resolved["attachment"] == MaterializedFile(
            b"%PDF-1.7", "q3.pdf", "application/pdf"
        )
        assert resolved["body"] == "hi"
        assert pod.reads == [("path", pod_id, "/me/q3.pdf", ctx)]

    @pytest.mark.asyncio
    async def test_several_attachments_and_a_file_id(self):
        pod_id = uuid4()
        pod = _Pod({"/me/a.txt": (b"a", "text/plain")}, pod_id=pod_id)

        resolved = await FileInputResolver(pod, pod_id=pod_id, ctx=None).resolve(
            GMAIL_SEND,
            {"attachment": [{"pod_path": "/me/a.txt"}, {"file_id": str(uuid4())}]},
        )

        assert [f.filename for f in resolved["attachment"]] == ["a.txt", "by-id.pdf"]

    @pytest.mark.asyncio
    async def test_the_payload_given_is_not_mutated(self):
        pod_id = uuid4()
        pod = _Pod({"/me/a.txt": (b"a", "text/plain")}, pod_id=pod_id)
        payload = {"attachment": {"pod_path": "/me/a.txt"}}

        await FileInputResolver(pod, pod_id=pod_id, ctx=None).resolve(
            GMAIL_SEND, payload
        )

        assert payload == {"attachment": {"pod_path": "/me/a.txt"}}

    @pytest.mark.asyncio
    async def test_a_file_the_caller_cannot_read_is_refused_by_field(self):
        pod_id = uuid4()
        resolver = FileInputResolver(_Pod({}, pod_id=pod_id), pod_id=pod_id, ctx=None)

        with pytest.raises(ConnectorValidationError) as raised:
            await resolver.resolve(DRIVE_UPLOAD, {"file_to_upload": {"pod_path": "/x"}})

        assert raised.value.details["field"] == "$.file_to_upload"

    @pytest.mark.asyncio
    async def test_a_pod_path_without_a_pod_says_how_to_fix_it(self):
        resolver = FileInputResolver(None, pod_id=None, ctx=None)

        with pytest.raises(ConnectorValidationError) as raised:
            await resolver.resolve(
                DRIVE_UPLOAD, {"file_to_upload": {"pod_path": "/me/a"}}
            )

        assert raised.value.details["reason"] == "file_input_needs_pod"

    @pytest.mark.asyncio
    async def test_inline_base64_needs_no_pod(self):
        resolver = FileInputResolver(None, pod_id=None, ctx=None)
        payload = {
            "file_to_upload": {
                "base64": base64.b64encode(b"csv,data").decode(),
                "filename": "report.csv",
            }
        }

        resolved = await resolver.resolve(DRIVE_UPLOAD, payload)

        assert resolved["file_to_upload"] == MaterializedFile(
            b"csv,data", "report.csv", "text/csv"
        )

    @pytest.mark.asyncio
    async def test_too_much_is_refused_before_anything_is_sent(self, monkeypatch):
        from app.modules.connectors.config import connector_settings

        monkeypatch.setattr(connector_settings, "connector_file_input_max_bytes", 4)
        pod_id = uuid4()
        pod = _Pod({"/me/big": (b"12345", "text/plain")}, pod_id=pod_id)

        with pytest.raises(ConnectorValidationError) as raised:
            await FileInputResolver(pod, pod_id=pod_id, ctx=None).resolve(
                DRIVE_UPLOAD, {"file_to_upload": {"pod_path": "/me/big"}}
            )

        assert raised.value.details["reason"] == "file_input_too_large"


class TestOutputPathIsLemmas:
    def test_it_is_not_sent_to_a_provider_that_does_not_declare_it(self):
        payload, output_path = split_output_path(
            DRIVE_UPLOAD, {"file_id": "x", "output_path": "/me/out.pdf"}
        )
        assert payload == {"file_id": "x"}
        assert output_path == "/me/out.pdf"

    def test_it_stays_when_the_operation_declares_it(self):
        schema = {"properties": {"output_path": {"type": "string"}}}
        payload, output_path = split_output_path(schema, {"output_path": "/me/o"})
        assert payload == {"output_path": "/me/o"}
        assert output_path == "/me/o"


class TestComposioStaging:
    def test_each_file_is_staged_and_replaced_by_its_key(self, monkeypatch):
        presigns: list[dict] = []
        puts: list[tuple] = []

        def create_presigned_url(**kwargs):
            presigns.append(kwargs)
            return SimpleNamespace(
                key=f"staged/{kwargs['filename']}",
                new_presigned_url=f"https://storage.example/{kwargs['filename']}",
                metadata=SimpleNamespace(storage_backend="s3"),
            )

        composio = SimpleNamespace(
            client=SimpleNamespace(
                tools=SimpleNamespace(
                    retrieve=lambda slug: SimpleNamespace(
                        toolkit=SimpleNamespace(slug="gmail")
                    )
                ),
                files=SimpleNamespace(create_presigned_url=create_presigned_url),
            )
        )

        def fake_put(url, *, content, headers, timeout):
            puts.append((url, content, headers))
            return httpx.Response(200)

        monkeypatch.setattr(composio_operation_gateway.httpx, "put", fake_put)
        payload = {
            "recipient_email": "a@example.com",
            "attachment": [
                MaterializedFile(b"one", "one.pdf", "application/pdf"),
                MaterializedFile(b"two", "two.txt", "text/plain"),
            ],
        }

        staged = composio_operation_gateway.stage_files(
            composio, "GMAIL_SEND_EMAIL", payload
        )

        assert staged["attachment"] == [
            {
                "name": "one.pdf",
                "mimetype": "application/pdf",
                "s3key": "staged/one.pdf",
            },
            {"name": "two.txt", "mimetype": "text/plain", "s3key": "staged/two.txt"},
        ]
        assert staged["recipient_email"] == "a@example.com"
        assert {p["toolkit_slug"] for p in presigns} == {"gmail"}
        # The PUT carries the content type the URL was signed with.
        assert puts[0][2]["Content-Type"] == "application/pdf"

    def test_a_call_without_files_makes_no_extra_request(self):
        composio = SimpleNamespace()  # any attribute access would raise
        payload = {"recipient_email": "a@example.com"}
        assert composio_operation_gateway.stage_files(composio, "X", payload) is payload


@pytest.mark.asyncio
async def test_openapi_multipart_sends_the_files_name_and_type():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["content"] = request.content
        return httpx.Response(201, json={"uploaded": True})

    executor = OpenApiHttpExecutor(
        client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
    )
    await executor.execute(
        connector_id="drive",
        operation_name="upload",
        execution={
            "mode": "openapi",
            "method": "POST",
            "path": "/upload",
            "server_url": "https://api.example.com",
            "path_params": [],
            "query_params": [],
            "header_params": [],
            "request_body": {
                "content_type": "multipart/form-data",
                "field": "body",
                "binary_fields": ["file"],
                "form_fields": [],
            },
            "response": {"binary": False},
        },
        payload={
            "body": {"file": MaterializedFile(b"%PDF", "q3.pdf", "application/pdf")}
        },
        third_party_credentials={"access_token": "t", "token_type": "Bearer"},
    )

    assert b'filename="q3.pdf"' in seen["content"]
    assert b"Content-Type: application/pdf" in seen["content"]


def test_mcp_receives_files_as_base64():
    args = _as_tool_arguments({"doc": MaterializedFile(b"hi", "a.txt", "text/plain")})
    assert args == {"doc": base64.b64encode(b"hi").decode()}


class TestGmailSendWithAttachments:
    """Native Gmail takes a whole base64url RFC 822 message and nothing else,
    so an attachment meant a model building multipart MIME by hand."""

    def _config_op(self, name: str) -> dict:
        import json
        from pathlib import Path

        config = (
            Path(__file__).resolve().parents[5] / "scripts" / "lemma_apps_config.json"
        )
        gmail = next(a for a in json.loads(config.read_text()) if a["name"] == "gmail")
        return next(op for op in gmail["static_operations"] if op["name"] == name)

    @pytest.mark.asyncio
    async def test_send_message_builds_the_message_the_api_wants(self):
        from email import message_from_bytes, policy

        seen = {}

        def handler(request: httpx.Request) -> httpx.Response:
            import json

            seen["url"] = str(request.url)
            seen["body"] = json.loads(request.content)
            return httpx.Response(200, json={"id": "m1", "threadId": "t1"})

        op = self._config_op("send_message")
        pod_id = uuid4()
        pod = _Pod({"/me/q3.pdf": (b"%PDF-1.7 q3", "application/pdf")}, pod_id=pod_id)
        payload = await FileInputResolver(pod, pod_id=pod_id, ctx=None).resolve(
            op["input_schema"],
            {
                "to": ["ada@example.com", "bob@example.com"],
                "subject": "Q3 report",
                "text": "Attached.",
                "attachments": [{"pod_path": "/me/q3.pdf"}],
                "thread_id": "t0",
            },
        )
        executor = OpenApiHttpExecutor(
            client=httpx.AsyncClient(transport=httpx.MockTransport(handler))
        )

        await executor.execute(
            connector_id="gmail",
            operation_name="send_message",
            execution=op["execution"],
            payload=payload,
            third_party_credentials={"access_token": "t", "token_type": "Bearer"},
        )

        assert seen["url"].endswith("/gmail/v1/users/me/messages/send")
        assert seen["body"]["threadId"] == "t0"
        raw = base64.urlsafe_b64decode(seen["body"]["raw"])
        message = message_from_bytes(raw, policy=policy.default)
        assert message["To"] == "ada@example.com, bob@example.com"
        assert message["Subject"] == "Q3 report"
        assert message.get_body(("plain",)).get_content().strip() == "Attached."
        (attachment,) = list(message.iter_attachments())
        assert attachment.get_filename() == "q3.pdf"
        assert attachment.get_content_type() == "application/pdf"
        assert attachment.get_content() == b"%PDF-1.7 q3"

    def test_a_draft_wraps_the_message(self):
        from app.modules.connectors.infrastructure.adapters.mime_message import (
            rfc822_json_body,
        )

        body = rfc822_json_body(
            self._config_op("create_draft")["execution"]["request_body"],
            {"to": "ada@example.com", "subject": "draft"},
        )

        assert set(body) == {"message"}
        assert "raw" in body["message"]

    def test_the_attachments_field_is_shown_as_pod_references(self):
        presented = present_input_schema(
            self._config_op("send_message")["input_schema"]
        )
        items = presented["properties"]["attachments"]["items"]
        assert items[FILE_MARKER] is True
        assert "pod_path" in items["properties"]


def test_presenting_twice_changes_nothing():
    """`OperationDetail` presents on construction, and a detail can be built
    from one that already was -- the hint must not pile up."""
    once = present_input_schema(GMAIL_SEND)
    assert present_input_schema(once) == once


def test_every_operation_detail_shows_references():
    from app.modules.connectors.api.schemas.connector_operation_schemas import (
        OperationDetail,
    )

    detail = OperationDetail(name="GOOGLEDRIVE_UPLOAD_FILE", input_schema=DRIVE_UPLOAD)
    assert detail.input_schema["properties"]["file_to_upload"][FILE_MARKER] is True
