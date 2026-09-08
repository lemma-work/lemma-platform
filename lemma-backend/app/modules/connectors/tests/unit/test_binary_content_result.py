"""The shape every connector kind hands back for a download.

Its field names are a wire contract -- file capture, the agent harness and the
pod-bundle fetcher all read the serialized dict rather than importing the class
-- so what is asserted here is the serialized form, not just the constructor.
"""

from __future__ import annotations

import base64

import httpx
import pytest

from app.modules.connectors.domain.results import BinaryContentResult

pytestmark = pytest.mark.unit


def test_the_serialized_shape_is_the_contract_its_readers_depend_on():
    result = BinaryContentResult.from_bytes(
        b"hello", media_type="text/plain", file_name="a.txt"
    )

    assert result.model_dump() == {
        "type": "binary_content",
        "content_base64": base64.b64encode(b"hello").decode(),
        "media_type": "text/plain",
        "file_name": "a.txt",
        "size_bytes": 5,
        "content_disposition": None,
    }


def test_an_absent_media_type_falls_back_rather_than_being_empty():
    assert (
        BinaryContentResult.from_bytes(b"x", media_type="   ").media_type
        == "application/octet-stream"
    )


def test_a_response_carries_its_headers_into_the_result():
    response = httpx.Response(
        200,
        content=b"%PDF-1.7",
        headers={
            "content-type": "application/pdf",
            "content-disposition": 'attachment; filename="report.pdf"',
        },
    )

    result = BinaryContentResult.from_http_response(response)

    assert result.media_type == "application/pdf"
    assert result.file_name == "report.pdf"
    assert result.size_bytes == 8


@pytest.mark.parametrize(
    ("disposition", "expected"),
    [
        # The plain form, quoted and bare.
        ('attachment; filename="report.pdf"', "report.pdf"),
        ("inline; filename=report.pdf", "report.pdf"),
        # RFC 5987: a charset and a percent-encoded value. Returned verbatim,
        # the saved file would be called `report%20Q1.pdf`.
        ("attachment; filename*=UTF-8''report%20Q1.pdf", "report Q1.pdf"),
        ("attachment; filename*=UTF-8'en'%C2%A3%20rates.pdf", "\u00a3 rates.pdf"),
        # Parameter names are case-insensitive and whitespace is allowed.
        ('attachment; FILENAME = "report.pdf"', "report.pdf"),
        # A quoted value may contain the parameter separator.
        ('attachment; filename="with;semi.pdf"', "with;semi.pdf"),
        # Both forms, which senders do so a client understanding only one still
        # gets a name. RFC 6266 4.3 says take the extended one -- in either
        # order, because `Message.get_filename` takes whichever came first.
        (
            "attachment; filename=\"plain.pdf\"; filename*=UTF-8''fancy%20name.pdf",
            "fancy name.pdf",
        ),
        (
            "attachment; filename*=UTF-8''fancy%20name.pdf; filename=\"plain.pdf\"",
            "fancy name.pdf",
        ),
        # Nothing to take a name from.
        ("attachment", None),
        ('attachment; filename="   "', None),
    ],
)
def test_the_filename_is_read_out_of_the_header_grammar(disposition, expected):
    """`Content-Disposition` is a structured header, not a string to match.

    Every case here is one a regex over `filename=` gets wrong or drops.
    """
    response = httpx.Response(
        200, content=b"x", headers={"content-disposition": disposition}
    )

    assert BinaryContentResult.from_http_response(response).file_name == expected


@pytest.mark.parametrize(
    "header_name",
    ["content-type", "Content-Type", "CONTENT-TYPE", "Content-type"],
)
def test_headers_are_read_whatever_their_casing(header_name: str):
    """httpx's own mapping is case-insensitive; a hand-built dict is not, and
    that is the shape a caller passes when standing in for a response."""

    class _Response:
        headers = {header_name: "application/pdf"}
        content = b"x"

    assert BinaryContentResult.from_http_response(_Response()).media_type == (
        "application/pdf"
    )


def test_an_empty_body_is_still_a_result_rather_than_a_failure():
    response = httpx.Response(200, content=b"", headers={"content-type": "text/plain"})

    result = BinaryContentResult.from_http_response(response)

    assert result.size_bytes == 0
    assert result.content_base64 == ""
