"""Holistic E2E tests for file URL endpoints.

Covers both URL kinds end to end against the real app + Redis:

- the authenticated frontend deep-link (``GET .../files/url`` → ``app_url``), and
- the public, hit-capped short signed URL (``POST .../files/signed-url`` minted,
  served at ``GET /s/{code}``) — exercising defaults, custom values, server-side
  clamping, exact-byte serving, the hit cap, expiry, and member authorization.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from urllib.parse import quote

import pytest
from fastapi import status
from httpx import AsyncClient

from app.modules.datastore.tests.e2e.harness import DatastoreApi

pytestmark = pytest.mark.e2e

FILES = "/pods/{pod_id}/datastore/files"


def _parse_dt(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def _ttl_seconds(expires_at: str) -> float:
    return (_parse_dt(expires_at) - datetime.now(timezone.utc)).total_seconds()


def _code_of(signed_url: str) -> str:
    return signed_url.rstrip("/").rsplit("/", 1)[-1]


async def _upload(api: DatastoreApi, folder: str, name: str, content: bytes, **kw):
    f = await api.create_folder(folder)
    return await api.upload_file(name, content, directory_path=f["path"], **kw)


class TestAuthenticatedAppUrl:
    @pytest.mark.asyncio
    async def test_returns_download_url_and_app_url(self, pod_api: DatastoreApi):
        uploaded = await _upload(pod_api, "/me/urltest", "guide.md", b"hello world")
        path = uploaded["path"]

        resp = await pod_api.request(
            "GET", FILES.format(pod_id=pod_api.pod_id) + "/url", params={"path": path}
        )
        assert resp.status_code == status.HTTP_200_OK, resp.text
        body = resp.json()
        assert body["path"] == path  # public /me path, not the internal user-id path
        assert body["url"]
        assert body["app_url"].endswith(
            f"/pod/{pod_api.pod_id}/files?file={quote(path)}"
        )
        assert _ttl_seconds(body["expires_at"]) > 0

    @pytest.mark.asyncio
    async def test_app_url_url_encodes_special_characters(self, pod_api: DatastoreApi):
        uploaded = await _upload(pod_api, "/me/url enc", "weekly report.md", b"x")
        path = uploaded["path"]

        resp = await pod_api.request(
            "GET", FILES.format(pod_id=pod_api.pod_id) + "/url", params={"path": path}
        )
        assert resp.status_code == status.HTTP_200_OK, resp.text
        app_url = resp.json()["app_url"]
        assert "%20" in app_url  # spaces encoded
        assert " " not in app_url

    @pytest.mark.asyncio
    async def test_folder_has_no_url(self, pod_api: DatastoreApi):
        folder = await pod_api.create_folder("/me/folderurl")
        resp = await pod_api.request(
            "GET",
            FILES.format(pod_id=pod_api.pod_id) + "/url",
            params={"path": folder["path"]},
        )
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.text


class TestSignedUrlCreation:
    async def _sign(self, api: DatastoreApi, path: str, body: dict):
        return await api.request(
            "POST",
            FILES.format(pod_id=api.pod_id) + "/signed-url",
            params={"path": path},
            json=body,
        )

    @pytest.mark.asyncio
    async def test_defaults_apply_when_unspecified(self, pod_api: DatastoreApi):
        uploaded = await _upload(
            pod_api, "/me/def", "a.txt", b"a", content_type="text/plain"
        )
        resp = await self._sign(pod_api, uploaded["path"], {})
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        body = resp.json()
        assert body["max_hits"] == 200  # default
        # default expiry 24h, allow generous slack for slow CI
        assert 86400 - 120 <= _ttl_seconds(body["expires_at"]) <= 86400 + 120

    @pytest.mark.asyncio
    async def test_custom_values_respected(self, pod_api: DatastoreApi):
        uploaded = await _upload(
            pod_api, "/me/cust", "b.txt", b"b", content_type="text/plain"
        )
        resp = await self._sign(
            pod_api, uploaded["path"], {"expires_seconds": 3600, "max_hits": 7}
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        body = resp.json()
        assert body["max_hits"] == 7
        assert 3600 - 120 <= _ttl_seconds(body["expires_at"]) <= 3600 + 120

    @pytest.mark.asyncio
    async def test_clamps_max_hits_and_expiry_to_ceilings(self, pod_api: DatastoreApi):
        uploaded = await _upload(
            pod_api, "/me/clamp", "c.txt", b"c", content_type="text/plain"
        )
        resp = await self._sign(
            pod_api, uploaded["path"], {"expires_seconds": 604800, "max_hits": 1000}
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        body = resp.json()
        assert body["max_hits"] == 1000  # ceiling
        # expiry clamped to 7d (604800s)
        assert 604800 - 120 <= _ttl_seconds(body["expires_at"]) <= 604800 + 120

    @pytest.mark.asyncio
    async def test_out_of_range_inputs_are_rejected_by_the_schema(
        self, pod_api: DatastoreApi
    ):
        """The bounds are now in the OpenAPI document, not only in the clamp.

        The service still clamps — internal callers pass values straight
        through — but an API client gets told its input was wrong instead of
        silently receiving a different link from the one it asked for.
        """
        uploaded = await _upload(
            pod_api, "/me/floor", "d.txt", b"d", content_type="text/plain"
        )
        for body in ({"expires_seconds": 0}, {"max_hits": 0}, {"max_hits": 10**6}):
            resp = await self._sign(pod_api, uploaded["path"], body)
            assert resp.status_code == status.HTTP_422_UNPROCESSABLE_CONTENT, resp.text

    @pytest.mark.asyncio
    async def test_folder_cannot_be_signed(self, pod_api: DatastoreApi):
        folder = await pod_api.create_folder("/me/folder-sign")
        resp = await self._sign(pod_api, folder["path"], {})
        assert resp.status_code == status.HTTP_400_BAD_REQUEST, resp.text


class TestSignedUrlServing:
    async def _sign(self, api: DatastoreApi, path: str, body: dict) -> dict:
        resp = await api.request(
            "POST",
            FILES.format(pod_id=api.pod_id) + "/signed-url",
            params={"path": path},
            json=body,
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        return resp.json()

    @pytest.mark.asyncio
    async def test_serves_exact_bytes_without_auth(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        content = bytes(range(256)) * 8  # 2 KiB of binary, every byte value
        uploaded = await _upload(
            pod_api,
            "/me/bin",
            "blob.dat",
            content,
            content_type="application/octet-stream",
        )
        body = await self._sign(pod_api, uploaded["path"], {"max_hits": 5})

        # No auth headers on async_client — the code is the only capability.
        served = await async_client.get(f"/s/{_code_of(body['signed_url'])}")
        assert served.status_code == status.HTTP_200_OK, served.text
        assert served.content == content
        etag = f'"{uploaded["content_sha256"]}"'
        assert served.headers["etag"] == etag
        assert served.headers["cache-control"] == "private, no-cache"

        assert served.headers["accept-ranges"] == "bytes"
        assert served.headers["content-length"] == str(len(content))
        assert served.headers["x-content-type-options"] == "nosniff"

        not_modified = await async_client.get(
            f"/s/{_code_of(body['signed_url'])}",
            headers={"If-None-Match": etag},
        )
        assert not_modified.status_code == status.HTTP_304_NOT_MODIFIED
        assert not not_modified.content

    @pytest.mark.asyncio
    async def test_hit_cap_enforced_then_gone_then_not_found(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        content = b"capped payload"
        uploaded = await _upload(
            pod_api, "/me/cap", "r.txt", content, content_type="text/plain"
        )
        body = await self._sign(pod_api, uploaded["path"], {"max_hits": 2})
        code = _code_of(body["signed_url"])

        # Exactly max_hits successful fetches.
        for _ in range(2):
            ok = await async_client.get(f"/s/{code}")
            assert ok.status_code == status.HTTP_200_OK, ok.text
            assert ok.content == content

        # One past the cap → 410 Gone (and the link is burned)…
        gone = await async_client.get(f"/s/{code}")
        assert gone.status_code == status.HTTP_410_GONE

        # …then the burned code is simply unknown → 404.
        after = await async_client.get(f"/s/{code}")
        assert after.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_expired_link_returns_404(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        uploaded = await _upload(
            pod_api, "/me/exp", "e.txt", b"soon gone", content_type="text/plain"
        )
        body = await self._sign(
            pod_api, uploaded["path"], {"expires_seconds": 1, "max_hits": 100}
        )
        code = _code_of(body["signed_url"])

        # Let the 1s Redis TTL lapse, then the link is gone regardless of hits left.
        await asyncio.sleep(1.4)
        expired = await async_client.get(f"/s/{code}")
        assert expired.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_unknown_code_is_404(self, async_client: AsyncClient):
        resp = await async_client.get("/s/this-code-does-not-exist")
        assert resp.status_code == status.HTTP_404_NOT_FOUND


class TestSignedUrlAuthorization:
    @pytest.mark.asyncio
    async def test_member_with_read_can_sign_and_serve_shared_file(
        self, pod_api: DatastoreApi, async_client: AsyncClient, member_users
    ):
        """A viewer (read-only member) can mint and use a link for a shared file."""
        content = b"shared, signed by viewer"
        uploaded = await _upload(
            pod_api, "/shared", "memo.txt", content, content_type="text/plain"
        )

        viewer = DatastoreApi(async_client, pod_api.pod_id, member_users["viewer"])
        resp = await viewer.request(
            "POST",
            FILES.format(pod_id=pod_api.pod_id) + "/signed-url",
            params={"path": uploaded["path"]},
            json={"max_hits": 3},
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        served = await async_client.get(f"/s/{_code_of(resp.json()['signed_url'])}")
        assert served.status_code == status.HTTP_200_OK
        assert served.content == content


class TestSignedUrlBrowserBehaviour:
    """The parts that only matter because a browser, not an SDK, opens these."""

    async def _sign(self, api: DatastoreApi, path: str, body: dict) -> dict:
        resp = await api.request(
            "POST",
            FILES.format(pod_id=api.pod_id) + "/signed-url",
            params={"path": path},
            json=body,
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        return resp.json()

    @pytest.mark.asyncio
    async def test_range_request_returns_partial_content(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """Without this Safari will not play a shared video at all."""
        content = b"0123456789abcdef"
        uploaded = await _upload(
            pod_api, "/me/rng", "clip.bin", content, content_type="video/mp4"
        )
        body = await self._sign(pod_api, uploaded["path"], {})
        code = _code_of(body["signed_url"])

        resp = await async_client.get(f"/s/{code}", headers={"Range": "bytes=4-7"})
        assert resp.status_code == status.HTTP_206_PARTIAL_CONTENT, resp.text
        assert resp.content == b"4567"
        assert resp.headers["content-range"] == f"bytes 4-7/{len(content)}"
        assert resp.headers["content-length"] == "4"

        tail = await async_client.get(f"/s/{code}", headers={"Range": "bytes=-3"})
        assert tail.status_code == status.HTTP_206_PARTIAL_CONTENT
        assert tail.content == b"def"

    @pytest.mark.asyncio
    async def test_unsatisfiable_range_is_416(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        content = b"tiny"
        uploaded = await _upload(
            pod_api, "/me/rng416", "t.bin", content, content_type="video/mp4"
        )
        body = await self._sign(pod_api, uploaded["path"], {})

        resp = await async_client.get(
            f"/s/{_code_of(body['signed_url'])}", headers={"Range": "bytes=99-"}
        )
        assert resp.status_code == status.HTTP_416_RANGE_NOT_SATISFIABLE
        assert resp.headers["content-range"] == f"bytes */{len(content)}"

    @pytest.mark.asyncio
    async def test_html_is_downloaded_not_rendered(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """An inline `.html` here would be script on the session-cookie origin."""
        uploaded = await _upload(
            pod_api,
            "/me/active",
            "page.html",
            b"<script>alert(1)</script>",
            content_type="text/html",
        )
        body = await self._sign(pod_api, uploaded["path"], {})

        resp = await async_client.get(f"/s/{_code_of(body['signed_url'])}")
        assert resp.status_code == status.HTTP_200_OK, resp.text
        assert resp.headers["content-disposition"].startswith("attachment")
        assert resp.headers["x-content-type-options"] == "nosniff"

    @pytest.mark.asyncio
    async def test_pdf_renders_in_place(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        uploaded = await _upload(
            pod_api,
            "/me/inline",
            "report.pdf",
            b"%PDF-1.4 ",
            content_type="application/pdf",
        )
        body = await self._sign(pod_api, uploaded["path"], {})

        resp = await async_client.get(f"/s/{_code_of(body['signed_url'])}")
        assert resp.status_code == status.HTTP_200_OK, resp.text
        assert resp.headers["content-disposition"].startswith("inline")
        assert resp.headers["content-type"].startswith("application/pdf")

    @pytest.mark.asyncio
    async def test_non_ascii_filename_serves_instead_of_500ing(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """Header values are encoded latin-1, so an unescaped CJK name 500'd."""
        uploaded = await _upload(
            pod_api, "/me/cjk", "报告.pdf", b"%PDF-1.4 ", content_type="application/pdf"
        )
        body = await self._sign(pod_api, uploaded["path"], {})

        resp = await async_client.get(f"/s/{_code_of(body['signed_url'])}")
        assert resp.status_code == status.HTTP_200_OK, resp.text
        disposition = resp.headers["content-disposition"]
        assert "filename*=UTF-8''" in disposition
        assert 'filename="' in disposition  # ASCII fallback for old clients

    @pytest.mark.asyncio
    async def test_content_type_comes_from_the_record(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """The served type is the recorded one, not a fresh guess at the key.

        `.wav` is where the two visibly disagree: the repo's own extension map
        says `audio/wav`, while `mimetypes.guess_type` — what this route used to
        call — says the legacy `audio/x-wav`. The record is authoritative
        because it is the only thing that survives a file whose type was
        sniffed, converted or set by something other than its name.
        """
        uploaded = await _upload(
            pod_api, "/me/rec", "note.wav", b"RIFF....WAVE", content_type="audio/wav"
        )
        body = await self._sign(pod_api, uploaded["path"], {})

        resp = await async_client.get(f"/s/{_code_of(body['signed_url'])}")
        assert resp.status_code == status.HTTP_200_OK, resp.text
        assert resp.headers["content-type"].startswith("audio/wav")
        assert resp.headers["content-disposition"].startswith("inline")

    @pytest.mark.asyncio
    async def test_extensionless_file_is_offered_as_a_download(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """Nothing upstream can type a file called `README`, so it stays
        `application/octet-stream` — and an unknown type is never inline."""
        uploaded = await _upload(
            pod_api, "/me/noext", "README", b"# hi", content_type="text/plain"
        )
        body = await self._sign(pod_api, uploaded["path"], {})

        resp = await async_client.get(f"/s/{_code_of(body['signed_url'])}")
        assert resp.status_code == status.HTTP_200_OK, resp.text
        assert resp.headers["content-type"].startswith("application/octet-stream")
        assert resp.headers["content-disposition"].startswith("attachment")

    @pytest.mark.asyncio
    async def test_browser_gets_a_page_and_an_sdk_gets_json(
        self, async_client: AsyncClient
    ):
        html = await async_client.get(
            "/s/no-such-code", headers={"Accept": "text/html,*/*;q=0.8"}
        )
        assert html.status_code == status.HTTP_404_NOT_FOUND
        assert html.headers["content-type"].startswith("text/html")
        assert "expired" in html.text.lower()

        api = await async_client.get(
            "/s/no-such-code", headers={"Accept": "application/json"}
        )
        assert api.status_code == status.HTTP_404_NOT_FOUND
        assert api.headers["content-type"].startswith("application/json")

    @pytest.mark.asyncio
    async def test_bare_root_is_not_found_rather_than_unauthorized(
        self, async_client: AsyncClient
    ):
        """`/s` is a missing code, not an authentication problem.

        `TrailingSlashMiddleware` rewrites `/s/` to `/s`, which no longer
        matches the `/s/` auth exclusion — so this used to answer 401.
        """
        for path in ("/s", "/s/"):
            resp = await async_client.get(path)
            assert resp.status_code == status.HTTP_404_NOT_FOUND, path


class TestSignedUrlBudget:
    """Only bytes actually sent are charged against the link."""

    async def _sign(self, api: DatastoreApi, path: str, body: dict) -> dict:
        resp = await api.request(
            "POST",
            FILES.format(pod_id=api.pod_id) + "/signed-url",
            params={"path": path},
            json=body,
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        return resp.json()

    @pytest.mark.asyncio
    async def test_revalidation_and_head_are_free(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """`no-cache` makes a browser revalidate on every load, and unfurling
        bots send HEAD. Charging either would let a link kill itself.
        """
        content = b"budget payload"
        uploaded = await _upload(
            pod_api, "/me/budget", "b.txt", content, content_type="text/plain"
        )
        body = await self._sign(pod_api, uploaded["path"], {"max_hits": 1})
        code = _code_of(body["signed_url"])
        etag = f'"{uploaded["content_sha256"]}"'

        for _ in range(5):
            head = await async_client.head(f"/s/{code}")
            assert head.status_code == status.HTTP_200_OK
            revalidated = await async_client.get(
                f"/s/{code}", headers={"If-None-Match": etag}
            )
            assert revalidated.status_code == status.HTTP_304_NOT_MODIFIED

        # The one download the budget allows is still available.
        served = await async_client.get(f"/s/{code}")
        assert served.status_code == status.HTTP_200_OK
        assert served.content == content

        spent = await async_client.get(f"/s/{code}")
        assert spent.status_code == status.HTTP_410_GONE

    @pytest.mark.asyncio
    async def test_ranged_reads_cost_only_the_bytes_they_move(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """A player seeking through a file must not spend a download per seek."""
        content = bytes(range(200))
        uploaded = await _upload(
            pod_api, "/me/rngbudget", "seek.bin", content, content_type="video/mp4"
        )
        body = await self._sign(pod_api, uploaded["path"], {"max_hits": 1})
        code = _code_of(body["signed_url"])

        # Twenty 5-byte reads is one file's worth of bytes, so all must succeed.
        for start in range(0, 100, 5):
            resp = await async_client.get(
                f"/s/{code}", headers={"Range": f"bytes={start}-{start + 4}"}
            )
            assert resp.status_code == status.HTTP_206_PARTIAL_CONTENT, resp.text
            assert resp.content == content[start : start + 5]

    @pytest.mark.asyncio
    async def test_a_missing_object_costs_nothing(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """The old ordering incremented before storage was consulted, so a
        failure permanently spent a download."""
        uploaded = await _upload(
            pod_api, "/me/gone", "g.txt", b"here for now", content_type="text/plain"
        )
        body = await self._sign(pod_api, uploaded["path"], {"max_hits": 2})
        code = _code_of(body["signed_url"])

        await pod_api.delete_file(uploaded["path"])
        for _ in range(5):
            resp = await async_client.get(f"/s/{code}")
            assert resp.status_code == status.HTTP_404_NOT_FOUND
