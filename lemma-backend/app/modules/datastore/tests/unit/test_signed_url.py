"""Pure logic behind short file links: clamping, budgets, ranges, dispositions."""

from __future__ import annotations

import pytest

from app.modules.datastore.api.file_download_response import build_content_disposition
from app.modules.datastore.api.file_stream_response import (
    UNSATISFIABLE,
    is_inline_media_type,
    parse_byte_range,
)
from app.modules.datastore.domain.file_entities import DatastoreSignedLinkEntity
from app.modules.datastore.services.files.signed_url import _clamp


class TestClamp:
    def test_uses_default_when_none(self):
        assert _clamp(None, default=50, ceiling=100) == 50

    def test_caps_at_ceiling(self):
        assert _clamp(999, default=50, ceiling=100) == 100

    def test_floors_at_one(self):
        assert _clamp(0, default=50, ceiling=100) == 1
        assert _clamp(-5, default=50, ceiling=100) == 1

    def test_passes_through_value_in_range(self):
        assert _clamp(30, default=50, ceiling=100) == 30

    def test_seven_day_ceiling_is_reachable(self):
        assert _clamp(604800, default=86400, ceiling=604800) == 604800
        assert _clamp(10**9, default=86400, ceiling=604800) == 604800


class TestByteRange:
    """`parse_byte_range` returns a half-open range, UNSATISFIABLE, or None."""

    def test_closed_range_is_half_open(self):
        # `bytes=0-3` means four bytes, inclusive of 3 — hence end 4.
        assert parse_byte_range("bytes=0-3", 10) == (0, 4)

    def test_open_ended_range_runs_to_the_end(self):
        assert parse_byte_range("bytes=4-", 10) == (4, 10)

    def test_suffix_range_takes_the_last_n_bytes(self):
        assert parse_byte_range("bytes=-3", 10) == (7, 10)

    def test_suffix_longer_than_the_object_starts_at_zero(self):
        assert parse_byte_range("bytes=-99", 10) == (0, 10)

    def test_end_beyond_the_object_is_clamped(self):
        assert parse_byte_range("bytes=5-99", 10) == (5, 10)

    def test_start_at_or_past_the_end_is_unsatisfiable(self):
        assert parse_byte_range("bytes=10-", 10) == UNSATISFIABLE
        assert parse_byte_range("bytes=25-30", 10) == UNSATISFIABLE

    def test_zero_length_suffix_is_unsatisfiable(self):
        assert parse_byte_range("bytes=-0", 10) == UNSATISFIABLE

    def test_a_backwards_range_is_unsatisfiable(self):
        """`bytes=500-400` would otherwise yield a negative Content-Length."""
        assert parse_byte_range("bytes=5-4", 10) == UNSATISFIABLE
        assert parse_byte_range("bytes=5-5", 10) == (5, 6)  # one byte, not zero

    @pytest.mark.parametrize(
        "header",
        [
            None,
            "",
            "items=0-3",  # not a byte range
            "bytes=abc-def",
            "bytes=0",  # no separator
            "bytes=0-3,6-9",  # multi-range: ignored, serve the whole object
        ],
    )
    def test_unusable_headers_are_ignored_rather_than_rejected(self, header):
        """RFC 9110: a Range we cannot act on is ignored, not answered with 416.

        416 would break a request that a plain 200 answers perfectly well.
        """
        assert parse_byte_range(header, 10) is None

    def test_no_range_is_possible_on_an_unknown_size(self):
        assert parse_byte_range("bytes=0-3", 0) is None


class TestInlineMediaType:
    @pytest.mark.parametrize(
        "content_type",
        [
            "application/pdf",
            "image/png",
            "image/jpeg",
            "text/plain",
            "text/plain; charset=utf-8",
            "video/mp4",
            "audio/mpeg",
        ],
    )
    def test_inert_types_render_in_place(self, content_type):
        assert is_inline_media_type(content_type) is True

    @pytest.mark.parametrize(
        "content_type",
        [
            "text/html",
            "application/xhtml+xml",
            "image/svg+xml",  # an image that executes script
            "application/javascript",
            "application/octet-stream",
            "application/zip",
        ],
    )
    def test_active_and_unknown_types_are_downloaded(self, content_type):
        """Nothing that can execute may render on the API origin.

        `/s/` is served from the host that holds the session cookies and there
        is no CSP middleware behind it, so an inline `.html` or `.svg` would be
        script running with that origin's privileges.
        """
        assert is_inline_media_type(content_type) is False

    def test_the_check_is_case_insensitive(self):
        assert is_inline_media_type("IMAGE/PNG") is True
        assert is_inline_media_type("Image/SVG+XML") is False


class TestContentDisposition:
    def test_non_ascii_name_survives_as_a_latin1_safe_header(self):
        """A CJK filename used to 500: Starlette encodes header values as
        latin-1, so the raw f-string this route used raised UnicodeEncodeError.
        """
        header = build_content_disposition("inline", "报告.pdf")
        header.encode("latin-1")  # must not raise
        assert "filename*=UTF-8''" in header
        assert "%E6%8A%A5" in header

    def test_quote_in_name_cannot_break_out_of_the_header(self):
        header = build_content_disposition("attachment", 'evil".pdf')
        assert 'filename="evil_.pdf"' in header

    def test_name_with_no_ascii_at_all_still_has_a_fallback(self):
        header = build_content_disposition("inline", "报告")
        assert 'filename="download"' in header


class TestLinkLiveness:
    """`is_live` is what the rehydrate path consults when Redis has nothing."""

    @staticmethod
    def _link(**overrides) -> DatastoreSignedLinkEntity:
        from datetime import datetime, timedelta, timezone
        from uuid import uuid4

        defaults = {
            "id": uuid4(),
            "code": "abc123",
            "pod_id": uuid4(),
            "path": "/me/f.txt",
            "object_key": "pods/p/files/me/f.txt",
            "content_type": "text/plain",
            "filename": "f.txt",
            "size_bytes": 10,
            "max_hits": 5,
            "expires_at": datetime.now(timezone.utc) + timedelta(hours=1),
        }
        return DatastoreSignedLinkEntity(**{**defaults, **overrides})

    def test_a_fresh_link_is_live(self):
        assert self._link().is_live is True

    def test_expiry_kills_it(self):
        from datetime import datetime, timedelta, timezone

        expired = self._link(
            expires_at=datetime.now(timezone.utc) - timedelta(seconds=1)
        )
        assert expired.is_live is False

    def test_a_naive_expiry_is_read_as_utc(self):
        """Postgres can hand back a naive datetime; comparing it to an aware
        `now` raises rather than answering, which would 500 the serving route."""
        from datetime import datetime, timedelta

        naive = self._link(expires_at=datetime.utcnow() + timedelta(hours=1))
        assert naive.is_live is True

    def test_revocation_kills_it(self):
        from datetime import datetime, timezone

        assert self._link(revoked_at=datetime.now(timezone.utc)).is_live is False

    def test_a_spent_budget_kills_it_durably(self):
        """Without this the serving path resurrects an exhausted link.

        Spending the budget drops the Redis key, so the next fetch finds nothing
        cached and rehydrates from the row — which would hand back a fresh
        budget and serve the file again, for as long as the link had left.
        """
        from datetime import datetime, timezone

        assert self._link(exhausted_at=datetime.now(timezone.utc)).is_live is False
