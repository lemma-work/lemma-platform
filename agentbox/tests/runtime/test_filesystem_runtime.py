from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from agentbox.workspace_runtime.app import create_app


pytestmark = pytest.mark.asyncio
TOKEN = "runtime-filesystem-test-token"
HEADERS = {"X-AgentBox-Runtime-Token": TOKEN}


async def test_binary_file_crud_range_digest_and_listing(tmp_path: Path) -> None:
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)
    payload = bytes(range(256)) * 8192
    expected_digest = f"sha256:{hashlib.sha256(payload).hexdigest()}"
    source = str(tmp_path / "payload.bin")
    destination = str(tmp_path / "moved.bin")

    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        written = await client.put(
            "/files:content",
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            params={"path": source},
            content=payload,
        )
        ranged = await client.get(
            "/files:content",
            headers=HEADERS,
            params={"path": source, "offset": 123, "length": 4097},
        )
        listing = await client.get(
            "/files", headers=HEADERS, params={"path": str(tmp_path)}
        )
        moved = await client.post(
            "/files:move",
            headers=HEADERS,
            json={"source": source, "destination": destination},
        )
        stat = await client.get(
            "/files:stat", headers=HEADERS, params={"path": destination}
        )
        conflict = await client.put(
            "/files:content",
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            params={
                "path": destination,
                "expected_sha256": f"sha256:{'0' * 64}",
            },
            content=b"must-not-replace",
        )
        preserved = await client.get(
            "/files:content", headers=HEADERS, params={"path": destination}
        )
        deleted = await client.delete(
            "/files", headers=HEADERS, params={"path": destination}
        )

    assert written.status_code == 200
    assert written.json()["sha256"] == expected_digest
    assert written.json()["size_bytes"] == len(payload)
    assert ranged.content == payload[123 : 123 + 4097]
    assert listing.status_code == 200
    assert [entry["path"] for entry in listing.json()["entries"]] == [source]
    assert moved.status_code == 204
    assert stat.status_code == 200
    assert stat.json()["path"] == destination
    assert conflict.status_code == 409
    assert preserved.content == payload
    assert deleted.status_code == 204
    assert not (tmp_path / "moved.bin").exists()


async def test_write_and_move_create_missing_destination_directories(
    tmp_path: Path,
) -> None:
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)
    source = str(tmp_path / "nested" / "source.bin")
    destination = str(tmp_path / "other" / "deep" / "destination.bin")

    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        written = await client.put(
            "/files:content",
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            params={"path": source},
            content=b"nested",
        )
        moved = await client.post(
            "/files:move",
            headers=HEADERS,
            json={"source": source, "destination": destination},
        )
        content = await client.get(
            "/files:content",
            headers=HEADERS,
            params={"path": destination},
        )

    assert written.status_code == 200
    assert moved.status_code == 204
    assert content.content == b"nested"
    assert not (tmp_path / "nested" / "source.bin").exists()


async def test_filesystem_rejects_relative_and_symlink_escape(tmp_path: Path) -> None:
    root = tmp_path / "root"
    outside = tmp_path / "outside"
    root.mkdir()
    outside.mkdir()
    secret = outside / "secret"
    secret.write_bytes(b"outside")
    (root / "escape").symlink_to(secret)
    (root / "escape-parent").symlink_to(outside, target_is_directory=True)
    app = create_app(token=TOKEN, allowed_roots=(str(root),))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        relative = await client.get(
            "/files:content", headers=HEADERS, params={"path": "secret"}
        )
        escaped = await client.get(
            "/files:content",
            headers=HEADERS,
            params={"path": str(root / "escape")},
        )
        symlink_stat = await client.get(
            "/files:stat",
            headers=HEADERS,
            params={"path": str(root / "escape")},
        )
        write_through_symlink = await client.put(
            "/files:content",
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            params={"path": str(root / "escape-parent" / "stolen")},
            content=b"must-not-escape",
        )

    assert relative.status_code == 422
    assert escaped.status_code == 422
    assert symlink_stat.status_code == 200
    assert symlink_stat.json()["kind"] == "symlink"
    assert write_through_symlink.status_code == 422
    assert not (outside / "stolen").exists()
