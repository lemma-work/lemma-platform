"""The file protocol: finding files in payloads and in results.

Two things were broken and both were silent. Pod-file *inputs* were located by
inspecting the operation's execution descriptor, which Composio operations do
not have, so uploads never resolved for any Composio tool. Binary *outputs* were
detected by checking one specific shape at the top level, so Composio downloads
-- which nest their own envelope under ``data`` -- were never recognised, and
asking for one did nothing at all.
"""

from __future__ import annotations

import base64

import pytest

from app.modules.connectors.services.files.capture import (
    classify_binary,
    find_binary,
    replace_at,
)
from app.modules.connectors.services.files.file_ref import (
    FILE_MARKER,
    is_file_schema,
    iter_file_fields,
    parse_file_reference,
    set_in,
)

pytestmark = pytest.mark.unit

PNG = b"\x89PNG\r\n\x1a\n"


class TestRecognisingFileTypedInputs:
    def test_the_explicit_marker_is_recognised(self):
        assert is_file_schema({FILE_MARKER: True}) is True

    def test_openapi_binary_strings_are_recognised(self):
        # Specs imported before the marker existed still say it this way.
        assert is_file_schema({"type": "string", "format": "binary"}) is True

    def test_base64_content_encoding_is_recognised(self):
        assert is_file_schema({"type": "string", "contentEncoding": "base64"}) is True

    def test_a_marker_inside_a_oneof_is_recognised(self):
        schema = {"oneOf": [{"type": "string"}, {FILE_MARKER: True}]}
        assert is_file_schema(schema) is True

    def test_ordinary_fields_are_not_files(self):
        assert is_file_schema({"type": "string"}) is False
        assert is_file_schema(None) is False


class TestFindingFileFieldsInAPayload:
    def test_a_top_level_file_field_is_found(self):
        schema = {
            "type": "object",
            "properties": {"attachment": {FILE_MARKER: True}, "subject": {"type": "string"}},
        }
        payload = {"attachment": {"pod_path": "/me/x.pdf"}, "subject": "hi"}
        found = list(iter_file_fields(schema, payload))
        assert found == [(["attachment"], {"pod_path": "/me/x.pdf"})]

    def test_a_nested_file_field_is_found(self):
        # Multipart bodies put their file fields one level down under `body`.
        schema = {
            "type": "object",
            "properties": {
                "body": {
                    "type": "object",
                    "properties": {"file": {FILE_MARKER: True}},
                }
            },
        }
        payload = {"body": {"file": {"base64": "eA=="}}}
        assert list(iter_file_fields(schema, payload)) == [
            (["body", "file"], {"base64": "eA=="})
        ]

    def test_absent_and_null_fields_are_skipped(self):
        schema = {"type": "object", "properties": {"attachment": {FILE_MARKER: True}}}
        assert list(iter_file_fields(schema, {})) == []
        assert list(iter_file_fields(schema, {"attachment": None})) == []

    def test_it_works_without_an_execution_descriptor(self):
        # The whole reason this walks the input schema: Composio operations have
        # no execution descriptor, and the descriptor-driven version skipped
        # them entirely, so pod-file uploads silently never happened.
        schema = {"type": "object", "properties": {"file": {FILE_MARKER: True}}}
        assert list(iter_file_fields(schema, {"file": {"file_id": "abc"}}))

    def test_a_located_field_can_be_replaced_in_place(self):
        payload = {"body": {"file": {"pod_path": "/me/x.pdf"}}}
        set_in(payload, ["body", "file"], {"base64": "eA=="})
        assert payload == {"body": {"file": {"base64": "eA=="}}}


class TestParsingAReference:
    @pytest.mark.parametrize(
        ("value", "attribute"),
        [
            ({"pod_path": "/me/x.pdf"}, "pod_path"),
            ({"file_id": "0193"}, "file_id"),
            ({"base64": "eA=="}, "base64_data"),
            ({"url": "https://example.com/x.pdf"}, "url"),
        ],
    )
    def test_each_form_is_understood(self, value, attribute):
        reference = parse_file_reference(value)
        assert reference is not None and getattr(reference, attribute)

    def test_raw_bytes_are_understood(self):
        assert parse_file_reference(PNG).raw_bytes == PNG

    def test_a_plain_object_is_not_a_file_reference(self):
        assert parse_file_reference({"subject": "hello"}) is None
        assert parse_file_reference("just a string") is None

    def test_datastore_forms_are_flagged_as_needing_pod_context(self):
        # These cannot be resolved outside a pod, and knowing that up front is
        # what lets the resolve phase fetch them while a session is still bound.
        assert parse_file_reference({"pod_path": "/me/x"}).needs_pod_context is True
        assert parse_file_reference({"base64": "eA=="}).needs_pod_context is False


class TestRecognisingBinaryResults:
    def test_our_own_envelope_is_recognised(self):
        result = {
            "type": "binary_content",
            "content_base64": base64.b64encode(PNG).decode(),
            "media_type": "image/png",
        }
        found = classify_binary(result)
        assert found.source == "inline" and found.data == PNG

    def test_the_composio_envelope_is_recognised(self):
        # This is the shape a Google Drive download actually returns. It was
        # never matched before, so the download quietly did nothing.
        found = classify_binary(
            {"name": "q4.pdf", "mimetype": "application/pdf", "s3url": "https://x/y"}
        )
        assert found.source == "url"
        assert found.filename == "q4.pdf"
        assert found.url == "https://x/y"

    def test_raw_bytes_are_recognised(self):
        assert classify_binary(PNG).data == PNG

    def test_a_nested_file_is_found(self):
        # Composio buries its envelope under `data`, sometimes deeper.
        result = {
            "successful": True,
            "data": {
                "response_data": {
                    "file": {"name": "q4.pdf", "mimetype": "application/pdf", "s3url": "https://x"}
                }
            },
        }
        found = find_binary(result)
        assert found is not None
        assert found.path == ["data", "response_data", "file"]

    def test_a_file_inside_a_list_is_found(self):
        result = {"attachments": [{"name": "a.pdf", "mimetype": "application/pdf", "s3url": "https://x"}]}
        found = find_binary(result)
        assert found.path == ["attachments", 0]

    def test_an_ordinary_result_yields_nothing(self):
        assert find_binary({"files": [{"id": "1", "name": "notes"}]}) is None

    def test_malformed_base64_is_not_treated_as_binary(self):
        assert classify_binary(
            {"type": "binary_content", "content_base64": "!!!not base64!!!"}
        ) is None

    def test_the_walk_is_depth_limited(self):
        # A provider response is data; a pathological one must not drive
        # unbounded recursion.
        deep: dict = {"name": "x.pdf", "mimetype": "text/plain", "s3url": "https://x"}
        for _ in range(30):
            deep = {"nested": deep}
        assert find_binary(deep) is None

    def test_a_found_file_can_be_replaced_with_a_reference(self):
        result = {"data": {"file": {"name": "q4.pdf", "mimetype": "application/pdf", "s3url": "https://x"}}}
        found = find_binary(result)
        replace_at(result, found.path, {"type": "pod_file", "pod_path": "/me/q4.pdf"})
        assert result["data"]["file"]["pod_path"] == "/me/q4.pdf"


class TestWhatTheCallerReceives:
    """Size decides, not whether the caller remembered to ask."""

    @staticmethod
    def _writer(gateway=None):
        from app.modules.connectors.services.files.capture_writer import (
            BinaryResultWriter,
        )

        return BinaryResultWriter(gateway)

    class _Gateway:
        def __init__(self):
            self.written = []

        async def write_bytes(self, *, pod_id, directory, name, content, media_type, ctx):
            self.written.append(
                {"directory": directory, "name": name, "size": len(content)}
            )
            return {
                "type": "pod_file",
                "pod_path": f"{directory}/{name}",
                "size_bytes": len(content),
                "media_type": media_type,
            }

    @pytest.mark.asyncio
    async def test_a_small_result_stays_inline(self):
        from uuid import uuid4

        result = {"file": {"type": "binary_content", "content_base64": base64.b64encode(PNG).decode()}}
        out = await self._writer().capture(
            result, connector_id="gmail", pod_id=uuid4(), ctx=None
        )
        assert out["file"]["type"] == "binary_content"

    @pytest.mark.asyncio
    async def test_a_large_result_is_persisted_and_returned_as_a_reference(
        self, monkeypatch
    ):
        from uuid import uuid4

        from app.modules.connectors.config import connector_settings

        monkeypatch.setattr(connector_settings, "connector_inline_result_max_bytes", 16)
        gateway = self._Gateway()
        big = b"x" * 128
        result = {"file": {"type": "binary_content", "content_base64": base64.b64encode(big).decode()}}

        out = await self._writer(gateway).capture(
            result, connector_id="google_drive", pod_id=uuid4(), ctx=None
        )
        # No base64 in the response, and nothing held in memory to serialize.
        assert out["file"]["type"] == "pod_file"
        assert gateway.written[0]["size"] == 128
        assert gateway.written[0]["directory"].startswith("/me/connector-downloads/google_drive/")

    @pytest.mark.asyncio
    async def test_output_path_chooses_the_destination(self, monkeypatch):
        from uuid import uuid4

        from app.modules.connectors.config import connector_settings

        monkeypatch.setattr(connector_settings, "connector_inline_result_max_bytes", 16)
        gateway = self._Gateway()
        result = {"file": {"type": "binary_content", "content_base64": base64.b64encode(b"y" * 64).decode()}}

        await self._writer(gateway).capture(
            result,
            connector_id="google_drive",
            pod_id=uuid4(),
            ctx=None,
            output_path="/me/reports/q4.pdf",
        )
        assert gateway.written[0] == {"directory": "/me/reports", "name": "q4.pdf", "size": 64}

    @pytest.mark.asyncio
    async def test_output_path_persists_even_a_small_file(self):
        from uuid import uuid4

        gateway = self._Gateway()
        result = {"file": {"type": "binary_content", "content_base64": base64.b64encode(PNG).decode()}}
        out = await self._writer(gateway).capture(
            result, connector_id="gmail", pod_id=uuid4(), ctx=None, output_path="/me/a.png"
        )
        assert out["file"]["type"] == "pod_file"

    @pytest.mark.asyncio
    async def test_a_result_over_the_hard_ceiling_is_refused(self, monkeypatch):
        from uuid import uuid4

        from app.modules.connectors.config import connector_settings
        from app.modules.connectors.domain.errors import (
            OperationExecutionValidationError,
        )

        monkeypatch.setattr(connector_settings, "connector_response_max_bytes", 8)
        result = {"file": {"type": "binary_content", "content_base64": base64.b64encode(b"z" * 64).decode()}}
        with pytest.raises(OperationExecutionValidationError):
            await self._writer().capture(
                result, connector_id="gmail", pod_id=uuid4(), ctx=None
            )

    @pytest.mark.asyncio
    async def test_a_result_with_no_file_is_returned_untouched(self):
        from uuid import uuid4

        result = {"files": [{"id": "1", "name": "notes"}]}
        assert await self._writer().capture(
            result, connector_id="gmail", pod_id=uuid4(), ctx=None
        ) == result

    @pytest.mark.asyncio
    async def test_without_pod_context_it_falls_back_to_inline(self):
        # A function running outside a pod still gets usable bytes rather than a
        # reference it could not resolve.
        result = {"file": {"type": "binary_content", "content_base64": base64.b64encode(PNG).decode()}}
        out = await self._writer(self._Gateway()).capture(
            result, connector_id="gmail", pod_id=None, ctx=None, output_path="/me/a.png"
        )
        assert out["file"]["type"] == "binary_content"
