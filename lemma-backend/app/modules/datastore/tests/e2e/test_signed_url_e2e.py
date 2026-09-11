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


class TestSignedUrlDurability:
    """The record outlives Redis; the counter deliberately does not."""

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
    async def test_link_survives_losing_its_redis_key(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """The whole reason the row exists.

        Deleting the key is what a Redis restart, failover or eviction looks
        like from here — the link must still resolve, rehydrated from the
        record.
        """
        from app.modules.datastore.services.files.signed_url import (
            get_signed_url_store,
        )

        content = b"durable payload"
        uploaded = await _upload(
            pod_api, "/me/durable", "d.txt", content, content_type="text/plain"
        )
        body = await self._sign(pod_api, uploaded["path"], {})
        code = _code_of(body["signed_url"])

        store = get_signed_url_store()
        redis = await store._get_redis()
        # Not inside the assert: `python -O` strips assertions, and the delete
        # *is* the scenario — stripped, this would pass without testing anything.
        dropped = await redis.delete(store._key(code))
        assert dropped == 1

        served = await async_client.get(f"/s/{code}")
        assert served.status_code == status.HTTP_200_OK, served.text
        assert served.content == content

        # And it is cached again, so the next fetch touches no database.
        assert await redis.exists(store._key(code)) == 1

    @pytest.mark.asyncio
    async def test_an_unknown_code_still_404s_rather_than_hitting_the_record_twice(
        self, async_client: AsyncClient
    ):
        resp = await async_client.get("/s/definitely-not-a-real-code")
        assert resp.status_code == status.HTTP_404_NOT_FOUND

    @pytest.mark.asyncio
    async def test_revoked_link_stops_resolving_and_stays_stopped(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        uploaded = await _upload(
            pod_api, "/me/revoke", "r.txt", b"secret", content_type="text/plain"
        )
        body = await self._sign(pod_api, uploaded["path"], {})
        code = _code_of(body["signed_url"])

        assert (await async_client.get(f"/s/{code}")).status_code == status.HTTP_200_OK

        revoked = await pod_api.request(
            "DELETE", FILES.format(pod_id=pod_api.pod_id) + f"/signed-urls/{code}"
        )
        assert revoked.status_code == status.HTTP_200_OK, revoked.text
        assert revoked.json() == {"code": code, "revoked": True}

        # Dead immediately, and the rehydrate path must not resurrect it.
        assert (
            await async_client.get(f"/s/{code}")
        ).status_code == status.HTTP_404_NOT_FOUND
        assert (
            await async_client.get(f"/s/{code}")
        ).status_code == status.HTTP_404_NOT_FOUND

        # Revoking again is reported, not an error.
        again = await pod_api.request(
            "DELETE", FILES.format(pod_id=pod_api.pod_id) + f"/signed-urls/{code}"
        )
        assert again.json()["revoked"] is False

    @pytest.mark.asyncio
    async def test_revoking_an_unknown_code_reports_rather_than_404s(
        self, pod_api: DatastoreApi
    ):
        """A 404 here would tell a caller which codes exist."""
        resp = await pod_api.request(
            "DELETE", FILES.format(pod_id=pod_api.pod_id) + "/signed-urls/nosuchcode"
        )
        assert resp.status_code == status.HTTP_200_OK, resp.text
        assert resp.json()["revoked"] is False

    @pytest.mark.asyncio
    async def test_pod_can_list_what_it_handed_out(self, pod_api: DatastoreApi):
        uploaded = await _upload(
            pod_api, "/me/listing", "l.txt", b"listed", content_type="text/plain"
        )
        body = await self._sign(pod_api, uploaded["path"], {"max_hits": 3})
        code = _code_of(body["signed_url"])

        listed = await pod_api.request(
            "GET", FILES.format(pod_id=pod_api.pod_id) + "/signed-urls"
        )
        assert listed.status_code == status.HTTP_200_OK, listed.text
        entry = next(
            (link for link in listed.json()["links"] if link["code"] == code), None
        )
        assert entry is not None, listed.json()
        assert entry["filename"] == "l.txt"
        assert entry["max_hits"] == 3
        assert entry["revoked_at"] is None

        await pod_api.request(
            "DELETE", FILES.format(pod_id=pod_api.pod_id) + f"/signed-urls/{code}"
        )

        live = await pod_api.request(
            "GET", FILES.format(pod_id=pod_api.pod_id) + "/signed-urls"
        )
        assert all(link["code"] != code for link in live.json()["links"])

        dead = await pod_api.request(
            "GET",
            FILES.format(pod_id=pod_api.pod_id) + "/signed-urls",
            params={"include_dead": "true"},
        )
        revoked = next(link for link in dead.json()["links"] if link["code"] == code)
        assert revoked["revoked_at"] is not None

    @pytest.mark.asyncio
    async def test_the_listing_does_not_hand_over_another_members_codes(
        self, pod_api: DatastoreApi, async_client: AsyncClient, member_users
    ):
        """Each row carries the `code`, which is the whole capability.

        A pod-wide listing would therefore let any member open any other
        member's links — including ones pointing at files only their owner can
        read, since personal paths are an authorization rule at the file layer.
        """
        uploaded = await _upload(
            pod_api, "/me/private", "mine.txt", b"mine", content_type="text/plain"
        )
        mine = await self._sign(pod_api, uploaded["path"], {})
        my_code = _code_of(mine["signed_url"])

        viewer = DatastoreApi(async_client, pod_api.pod_id, member_users["viewer"])
        theirs = await viewer.request(
            "GET",
            FILES.format(pod_id=pod_api.pod_id) + "/signed-urls",
            params={"include_dead": "true"},
        )
        assert theirs.status_code == status.HTTP_200_OK, theirs.text
        assert all(link["code"] != my_code for link in theirs.json()["links"])

    @pytest.mark.asyncio
    async def test_revoking_an_already_dead_link_reports_false(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """Exhausted counts as dead, the same as revoked or expired."""
        uploaded = await _upload(
            pod_api, "/me/deadrevoke", "d.txt", b"spent", content_type="text/plain"
        )
        minted = await pod_api.request(
            "POST",
            FILES.format(pod_id=pod_api.pod_id) + "/signed-url",
            params={"path": uploaded["path"]},
            json={"max_hits": 1},
        )
        code = _code_of(minted.json()["signed_url"])
        await async_client.get(f"/s/{code}")
        assert (
            await async_client.get(f"/s/{code}")
        ).status_code == status.HTTP_410_GONE

        resp = await pod_api.request(
            "DELETE", FILES.format(pod_id=pod_api.pod_id) + f"/signed-urls/{code}"
        )
        assert resp.status_code == status.HTTP_200_OK, resp.text
        assert resp.json()["revoked"] is False

    @pytest.mark.asyncio
    async def test_a_revoked_link_cannot_be_rehydrated_back_to_life(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """The tombstone, from the other side.

        Revoking drops the cache entry; the next fetch is a cache miss, which is
        exactly the path that rebuilds an entry from the record. It must not.
        """
        from app.modules.datastore.services.files.signed_url import (
            get_signed_url_store,
        )

        uploaded = await _upload(
            pod_api, "/me/tomb", "t.txt", b"tombstoned", content_type="text/plain"
        )
        body = await self._sign(pod_api, uploaded["path"], {})
        code = _code_of(body["signed_url"])

        await pod_api.request(
            "DELETE", FILES.format(pod_id=pod_api.pod_id) + f"/signed-urls/{code}"
        )

        store = get_signed_url_store()
        redis = await store._get_redis()
        assert await redis.exists(store._key(code)) == 0
        assert (
            await async_client.get(f"/s/{code}")
        ).status_code == status.HTTP_404_NOT_FOUND
        assert await redis.exists(store._key(code)) == 0

    @pytest.mark.asyncio
    async def test_another_pod_cannot_knock_out_this_pods_cached_link(
        self,
        pod_api: DatastoreApi,
        authenticated_client: AsyncClient,
        async_client: AsyncClient,
        fixed_test_org,
    ):
        """Revoking is pod-scoped in the row; the Redis keys are not.

        Invalidating regardless of whether the row update matched let a caller
        authorized for one pod delete another pod's cached entry and hold a
        tombstone over it — a link they have no rights to, unusable for as long
        as the tombstone lives, and repeatably so.
        """
        from app.modules.datastore.services.files.signed_url import (
            get_signed_url_store,
        )
        from app.modules.datastore.tests.e2e.harness import pod_payload

        uploaded = await _upload(
            pod_api, "/me/victim", "theirs.txt", b"theirs", content_type="text/plain"
        )
        body = await self._sign(pod_api, uploaded["path"], {})
        code = _code_of(body["signed_url"])

        store = get_signed_url_store()
        redis = await store._get_redis()
        assert await redis.exists(store._key(code)) == 1

        # A second pod the same user owns, so the membership check passes and
        # the request reaches the store — which is the only thing left to scope
        # it. The code belongs to the first pod.
        created = await authenticated_client.post(
            "/pods", json=pod_payload(fixed_test_org["id"])
        )
        assert created.status_code == status.HTTP_201_CREATED, created.text
        other = DatastoreApi(authenticated_client, created.json()["id"])

        resp = await other.request(
            "DELETE", FILES.format(pod_id=other.pod_id) + f"/signed-urls/{code}"
        )
        assert resp.status_code == status.HTTP_200_OK, resp.text
        assert resp.json()["revoked"] is False

        # Untouched: still cached, no tombstone, and still serving.
        assert await redis.exists(store._key(code)) == 1
        assert await redis.exists(store._tombstone_key(code)) == 0
        served = await async_client.get(f"/s/{code}")
        assert served.status_code == status.HTTP_200_OK, served.text

    @pytest.mark.asyncio
    async def test_a_request_larger_than_the_remaining_budget_is_refused(
        self, pod_api: DatastoreApi, async_client: AsyncClient
    ):
        """The budget is a ceiling, not a line one response may cross.

        With one download's worth of budget, a ranged read that would take the
        total past it must be refused rather than allowed to overshoot.
        """
        content = bytes(range(100))
        uploaded = await _upload(
            pod_api, "/me/ceiling", "c.bin", content, content_type="video/mp4"
        )
        minted = await pod_api.request(
            "POST",
            FILES.format(pod_id=pod_api.pod_id) + "/signed-url",
            params={"path": uploaded["path"]},
            json={"max_hits": 1},
        )
        code = _code_of(minted.json()["signed_url"])

        # 60 of 100 bytes spent; a further 60 would overshoot.
        first = await async_client.get(f"/s/{code}", headers={"Range": "bytes=0-59"})
        assert first.status_code == status.HTTP_206_PARTIAL_CONTENT, first.text
        second = await async_client.get(f"/s/{code}", headers={"Range": "bytes=0-59"})
        assert second.status_code == status.HTTP_410_GONE, second.text

    @pytest.mark.asyncio
    async def test_another_pod_cannot_revoke_this_pods_link(
        self, pod_api: DatastoreApi, async_client: AsyncClient, member_users
    ):
        """Revocation is scoped in the UPDATE, not checked beforehand."""
        uploaded = await _upload(
            pod_api, "/shared", "cross.txt", b"x", content_type="text/plain"
        )
        body = await self._sign(pod_api, uploaded["path"], {})
        code = _code_of(body["signed_url"])

        from uuid import uuid4

        other = await pod_api.request(
            "DELETE",
            FILES.format(pod_id=uuid4()) + f"/signed-urls/{code}",
        )
        assert other.status_code in (
            status.HTTP_403_FORBIDDEN,
            status.HTTP_404_NOT_FOUND,
        ), other.text
        # Still live for its own pod.
        assert (await async_client.get(f"/s/{code}")).status_code == status.HTTP_200_OK


class TestSignedUrlLiveLimit:
    """A person may only have so many live links at once."""

    async def _sign(self, api: DatastoreApi, path: str):
        return await api.request(
            "POST",
            FILES.format(pod_id=api.pod_id) + "/signed-url",
            params={"path": path},
            json={},
        )

    @pytest.mark.asyncio
    async def test_minting_past_the_limit_is_refused_and_says_why(
        self, pod_api: DatastoreApi, monkeypatch
    ):
        from app.modules.datastore.config import datastore_settings

        monkeypatch.setattr(
            datastore_settings, "datastore_signed_url_max_active_per_user", 2
        )
        uploaded = await _upload(
            pod_api, "/me/limit", "cap.txt", b"capped", content_type="text/plain"
        )

        for _ in range(2):
            ok = await self._sign(pod_api, uploaded["path"])
            assert ok.status_code == status.HTTP_201_CREATED, ok.text

        refused = await self._sign(pod_api, uploaded["path"])
        assert refused.status_code == status.HTTP_429_TOO_MANY_REQUESTS, refused.text
        body = refused.json()
        assert body["code"] == "DATASTORE_SIGNED_LINK_LIMIT"
        # Actionable, not just a refusal: an agent has to choose between
        # revoking one and waiting, and needs the numbers to decide.
        assert body["details"] == {"limit": 2, "live": 2}

    @pytest.mark.asyncio
    async def test_revoking_frees_a_slot_immediately(
        self, pod_api: DatastoreApi, monkeypatch
    ):
        """The limit counts live links, so it clears without waiting for expiry."""
        from app.modules.datastore.config import datastore_settings

        monkeypatch.setattr(
            datastore_settings, "datastore_signed_url_max_active_per_user", 1
        )
        uploaded = await _upload(
            pod_api, "/me/freeslot", "f.txt", b"free", content_type="text/plain"
        )

        first = await self._sign(pod_api, uploaded["path"])
        assert first.status_code == status.HTTP_201_CREATED, first.text
        code = _code_of(first.json()["signed_url"])

        assert (
            await self._sign(pod_api, uploaded["path"])
        ).status_code == status.HTTP_429_TOO_MANY_REQUESTS

        await pod_api.request(
            "DELETE", FILES.format(pod_id=pod_api.pod_id) + f"/signed-urls/{code}"
        )

        after = await self._sign(pod_api, uploaded["path"])
        assert after.status_code == status.HTTP_201_CREATED, after.text

    @pytest.mark.asyncio
    async def test_a_dead_link_does_not_hold_a_slot(
        self, pod_api: DatastoreApi, async_client: AsyncClient, monkeypatch
    ):
        """An exhausted link is dead, so it must not count against the limit.

        Otherwise the cap would fill up with links nobody can use and the only
        remedy would be revoking each one by hand.
        """
        from app.modules.datastore.config import datastore_settings

        monkeypatch.setattr(
            datastore_settings, "datastore_signed_url_max_active_per_user", 1
        )
        uploaded = await _upload(
            pod_api, "/me/deadslot", "d.txt", b"spend me", content_type="text/plain"
        )

        minted = await pod_api.request(
            "POST",
            FILES.format(pod_id=pod_api.pod_id) + "/signed-url",
            params={"path": uploaded["path"]},
            json={"max_hits": 1},
        )
        assert minted.status_code == status.HTTP_201_CREATED, minted.text
        code = _code_of(minted.json()["signed_url"])

        # Spend it: one download, then the next request exhausts it.
        assert (await async_client.get(f"/s/{code}")).status_code == status.HTTP_200_OK
        assert (
            await async_client.get(f"/s/{code}")
        ).status_code == status.HTTP_410_GONE

        after = await self._sign(pod_api, uploaded["path"])
        assert after.status_code == status.HTTP_201_CREATED, after.text

    @pytest.mark.asyncio
    async def test_a_concurrent_burst_cannot_run_away_past_the_limit(
        self, pod_api: DatastoreApi, monkeypatch
    ):
        """What the limit guarantees under concurrency, stated honestly.

        The check and the insert are one statement, so a burst cannot mint one
        link per request the way a separate count-then-insert did. It is not a
        hard serialization either: two statements executing at the same instant
        can both see room under READ COMMITTED, so a burst may end a little over
        the line. What it may not do is run away — and the limit is in force
        again as soon as the burst drains, which is what bounds abuse.

        Serializing properly was tried and rejected: a per-user
        `pg_advisory_xact_lock` makes every waiter hold its pooled connection,
        and a burst exhausted the pool and 500ed unrelated requests.
        """
        from app.modules.datastore.config import datastore_settings

        monkeypatch.setattr(
            datastore_settings, "datastore_signed_url_max_active_per_user", 3
        )
        uploaded = await _upload(
            pod_api, "/me/race", "r.txt", b"racing", content_type="text/plain"
        )

        results = await asyncio.gather(
            *(self._sign(pod_api, uploaded["path"]) for _ in range(20))
        )
        created = [r for r in results if r.status_code == status.HTTP_201_CREATED]
        refused = [
            r for r in results if r.status_code == status.HTTP_429_TOO_MANY_REQUESTS
        ]
        assert created, [r.status_code for r in results]
        # Nothing unexpected in between — every request either minted or was
        # told why it could not.
        assert len(created) + len(refused) == 20, [r.status_code for r in results]
        # The point: 20 requests did not produce anything like 20 links.
        assert len(refused) >= 10, len(created)

        # And once the burst has drained, the limit holds outright.
        after = await self._sign(pod_api, uploaded["path"])
        assert after.status_code == status.HTTP_429_TOO_MANY_REQUESTS, after.text

    @pytest.mark.asyncio
    async def test_the_limit_is_per_person_not_per_pod(
        self, pod_api: DatastoreApi, async_client: AsyncClient, member_users
    ):
        """One member at their limit must not stop another member sharing."""
        from app.modules.datastore.config import datastore_settings

        original = datastore_settings.datastore_signed_url_max_active_per_user
        datastore_settings.datastore_signed_url_max_active_per_user = 1
        try:
            uploaded = await _upload(
                pod_api, "/shared", "team.txt", b"team", content_type="text/plain"
            )
            first = await self._sign(pod_api, uploaded["path"])
            assert first.status_code == status.HTTP_201_CREATED, first.text
            assert (
                await self._sign(pod_api, uploaded["path"])
            ).status_code == status.HTTP_429_TOO_MANY_REQUESTS

            viewer = DatastoreApi(async_client, pod_api.pod_id, member_users["viewer"])
            theirs = await viewer.request(
                "POST",
                FILES.format(pod_id=pod_api.pod_id) + "/signed-url",
                params={"path": uploaded["path"]},
                json={},
            )
            assert theirs.status_code == status.HTTP_201_CREATED, theirs.text
        finally:
            datastore_settings.datastore_signed_url_max_active_per_user = original
