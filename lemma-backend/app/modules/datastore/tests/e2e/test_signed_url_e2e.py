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
        uploaded = await _upload(
            pod_api, "/me/url enc", "weekly report.md", b"x"
        )
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
        uploaded = await _upload(pod_api, "/me/def", "a.txt", b"a", content_type="text/plain")
        resp = await self._sign(pod_api, uploaded["path"], {})
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        body = resp.json()
        assert body["max_hits"] == 50  # default
        # default expiry 3h (10800s), allow generous slack for slow CI
        assert 10800 - 120 <= _ttl_seconds(body["expires_at"]) <= 10800 + 120

    @pytest.mark.asyncio
    async def test_custom_values_respected(self, pod_api: DatastoreApi):
        uploaded = await _upload(pod_api, "/me/cust", "b.txt", b"b", content_type="text/plain")
        resp = await self._sign(
            pod_api, uploaded["path"], {"expires_seconds": 3600, "max_hits": 7}
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        body = resp.json()
        assert body["max_hits"] == 7
        assert 3600 - 120 <= _ttl_seconds(body["expires_at"]) <= 3600 + 120

    @pytest.mark.asyncio
    async def test_clamps_max_hits_and_expiry_to_ceilings(self, pod_api: DatastoreApi):
        uploaded = await _upload(pod_api, "/me/clamp", "c.txt", b"c", content_type="text/plain")
        resp = await self._sign(
            pod_api, uploaded["path"], {"expires_seconds": 10**9, "max_hits": 10**6}
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        body = resp.json()
        assert body["max_hits"] == 100  # ceiling
        # expiry clamped to 24h (86400s)
        assert 86400 - 120 <= _ttl_seconds(body["expires_at"]) <= 86400 + 120

    @pytest.mark.asyncio
    async def test_floors_non_positive_inputs_to_one(self, pod_api: DatastoreApi):
        uploaded = await _upload(pod_api, "/me/floor", "d.txt", b"d", content_type="text/plain")
        resp = await self._sign(
            pod_api, uploaded["path"], {"expires_seconds": 0, "max_hits": 0}
        )
        assert resp.status_code == status.HTTP_201_CREATED, resp.text
        assert resp.json()["max_hits"] == 1

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
            pod_api, "/me/bin", "blob.dat", content, content_type="application/octet-stream"
        )
        body = await self._sign(pod_api, uploaded["path"], {"max_hits": 5})

        # No auth headers on async_client — the code is the only capability.
        served = await async_client.get(f"/s/{_code_of(body['signed_url'])}")
        assert served.status_code == status.HTTP_200_OK, served.text
        assert served.content == content
        etag = f'"{uploaded["content_sha256"]}"'
        assert served.headers["etag"] == etag
        assert served.headers["cache-control"] == "private, no-cache"

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
