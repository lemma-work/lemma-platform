from __future__ import annotations

import hashlib
from pathlib import Path

import httpx
import pytest

from sandbox_runtime.workspace.app import create_app


pytestmark = pytest.mark.asyncio
TOKEN = "runtime-filesystem-test-token"
HEADERS = {"X-Lemma-Runtime-Token": TOKEN}


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
    # 200 because something was there to remove. The endpoint used to answer
    # 204 either way and throw the answer away, which is why both
    # runtime-backed providers hard-coded "yes, deleted" for a path that had
    # never existed.
    assert deleted.status_code == 200
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


async def test_directory_creation_is_idempotent_and_conflicts_fail_closed(
    tmp_path: Path,
) -> None:
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)
    directory = tmp_path / "conversations" / "nested"
    conflict = tmp_path / "not-a-directory"
    conflict.write_text("file")

    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        first = await client.put(
            "/directories",
            headers=HEADERS,
            params={"path": str(directory)},
        )
        second = await client.put(
            "/directories",
            headers=HEADERS,
            params={"path": str(directory)},
        )
        root = await client.put(
            "/directories",
            headers=HEADERS,
            params={"path": str(tmp_path)},
        )
        rejected = await client.put(
            "/directories",
            headers=HEADERS,
            params={"path": str(conflict)},
        )

    assert first.status_code == 204
    assert second.status_code == 204
    assert root.status_code == 204
    assert directory.is_dir()
    assert rejected.status_code == 422


async def test_streaming_transfer_is_bounded_and_failed_upload_is_atomic(
    tmp_path: Path,
) -> None:
    transfer_limit = 2 * 1024 * 1024
    app = create_app(
        token=TOKEN,
        allowed_roots=(str(tmp_path),),
        max_file_transfer_bytes=transfer_limit,
    )
    transport = httpx.ASGITransport(app=app)
    target = tmp_path / "large.bin"
    chunk = b"x" * (128 * 1024)

    async def exact_payload():
        for _ in range(16):
            yield chunk

    async def oversized_payload():
        for _ in range(16):
            yield chunk
        yield b"overflow"

    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        written = await client.put(
            "/files:content",
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            params={"path": str(target)},
            content=exact_payload(),
        )
        oversized = await client.put(
            "/files:content",
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            params={"path": str(target)},
            content=oversized_payload(),
        )
        async with client.stream(
            "GET",
            "/files:content",
            headers=HEADERS,
            params={"path": str(target)},
        ) as response:
            downloaded = 0
            async for response_chunk in response.aiter_bytes():
                downloaded += len(response_chunk)

    assert written.status_code == 200
    assert written.json()["size_bytes"] == transfer_limit
    assert oversized.status_code == 413
    assert downloaded == transfer_limit
    assert target.stat().st_size == transfer_limit
    assert list(tmp_path.glob(".*.lemma-*")) == []


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


async def test_a_delivered_secret_is_not_readable_by_the_rest_of_the_sandbox(
    tmp_path: Path,
) -> None:
    """`mode` restricts the written file, and does so before it is visible.

    Docker delivers a secret as a 0600 tar entry and E2B chmods after writing;
    Desktop delivers through this endpoint, which took the runtime's umask and
    produced 0644. The browser relay token is delivered this way, so on Desktop
    alone every process in the sandbox could read it.
    """
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)
    secret = str(tmp_path / "relay.token")
    ordinary = str(tmp_path / "ordinary.txt")

    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        restricted = await client.put(
            "/files:content",
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            params={"path": secret, "mode": "600"},
            content=b"a-relay-token",
        )
        plain = await client.put(
            "/files:content",
            headers={**HEADERS, "Content-Type": "application/octet-stream"},
            params={"path": ordinary},
            content=b"not a secret",
        )

    assert restricted.status_code == 200
    assert plain.status_code == 200
    assert Path(secret).read_bytes() == b"a-relay-token"
    assert Path(secret).stat().st_mode & 0o777 == 0o600
    # Unrestricted writes are untouched by this: the parameter narrows a file,
    # it does not become a new default.
    assert Path(ordinary).stat().st_mode & 0o777 != 0o600


async def test_a_mode_that_is_not_three_octal_digits_is_refused(tmp_path: Path) -> None:
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)

    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        for rejected in ("4755", "abc", "8", "-600"):
            response = await client.put(
                "/files:content",
                headers={**HEADERS, "Content-Type": "application/octet-stream"},
                params={"path": str(tmp_path / "x.bin"), "mode": rejected},
                content=b"x",
            )
            assert response.status_code == 422, rejected


async def test_deleting_nothing_is_distinguishable_from_deleting_something(
    tmp_path: Path,
) -> None:
    """The contract is "whether anything was there to remove".

    The manager computed it and the endpoint discarded it, so `docker` and
    `lemma_local` both returned a hard-coded `True` and told callers a file had
    been deleted when there had never been one. E2B answered truthfully, so the
    same call meant different things on different fabrics.
    """
    app = create_app(token=TOKEN, allowed_roots=(str(tmp_path),))
    transport = httpx.ASGITransport(app=app)
    present = str(tmp_path / "present.txt")
    absent = str(tmp_path / "absent.txt")
    Path(present).write_text("x", encoding="utf-8")

    async with httpx.AsyncClient(
        transport=transport, base_url="http://runtime.test"
    ) as client:
        removed = await client.delete(
            "/files", headers=HEADERS, params={"path": present}
        )
        nothing = await client.delete(
            "/files", headers=HEADERS, params={"path": absent}
        )

    assert removed.status_code == 200, "a file that was there reports as removed"
    assert nothing.status_code == 204, (
        "a file that was not there reports as not removed"
    )
