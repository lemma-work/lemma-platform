"""Unit tests for the pure helpers behind GitHub publish/import — the parts
verifiable without a live Composio/GitHub connection."""

from __future__ import annotations

import base64
import io
import zipfile
from types import SimpleNamespace

import pytest

from app.modules.connectors.domain.errors import OperationExecutionError
from app.modules.pod_import.api.controllers.github_controller import (
    _archive_entries,
    _capability_bullets,
    _create_repo_with_retry,
    _extract_result,
    _github_repo_slug,
    _is_payload_too_large,
    _push_files_best_effort,
    _reassemble_chunked_entries,
    _render_readme,
    _strip_bundle_root,
)


class FakeUseCases:
    """Fakes execute_operation_for_auth_config: records the names it was asked
    to create, fails with a GitHub-style "already exists" error for any name
    in ``taken``, and otherwise "creates" the repo."""

    def __init__(self, taken: set[str]):
        self.taken = taken
        self.attempted_names: list[str] = []

    async def execute_operation_for_auth_config(self, *, operation_name, payload, **_kwargs):
        name = payload["name"]
        self.attempted_names.append(name)
        if name in self.taken:
            raise OperationExecutionError(
                f"Composio tool execution failed for '{operation_name}': "
                '{"message":"Repository creation failed.","errors":[{"resource":"Repository",'
                '"code":"custom","field":"name","message":"name already exists on this account"}]}'
            )
        return SimpleNamespace(result={"data": {"full_name": f"someone/{name}"}})


@pytest.mark.asyncio
async def test_create_repo_succeeds_immediately_when_name_is_free():
    use_cases = FakeUseCases(taken=set())
    repo = await _create_repo_with_retry(
        use_cases, {}, "trumpet", description="d", private=False
    )
    assert repo == {"full_name": "someone/trumpet"}
    assert use_cases.attempted_names == ["trumpet"]


@pytest.mark.asyncio
async def test_create_repo_retries_with_a_numbered_suffix_on_name_collision():
    use_cases = FakeUseCases(taken={"trumpet", "trumpet-2"})
    repo = await _create_repo_with_retry(
        use_cases, {}, "trumpet", description="d", private=False
    )
    assert repo == {"full_name": "someone/trumpet-3"}
    assert use_cases.attempted_names == ["trumpet", "trumpet-2", "trumpet-3"]


@pytest.mark.asyncio
async def test_create_repo_gives_up_after_max_attempts():
    use_cases = FakeUseCases(taken={"trumpet", "trumpet-2", "trumpet-3", "trumpet-4", "trumpet-5"})
    with pytest.raises(OperationExecutionError, match="already exists"):
        await _create_repo_with_retry(use_cases, {}, "trumpet", description="d", private=False)


@pytest.mark.asyncio
async def test_create_repo_does_not_retry_a_different_kind_of_error():
    class FailDifferently(FakeUseCases):
        async def execute_operation_for_auth_config(self, *, operation_name, payload, **_kwargs):
            raise OperationExecutionError("rate limited")

    use_cases = FailDifferently(taken=set())
    with pytest.raises(OperationExecutionError, match="rate limited"):
        await _create_repo_with_retry(use_cases, {}, "trumpet", description="d", private=False)
    assert use_cases.attempted_names == []


def test_repo_slug_strips_unsafe_characters():
    assert _github_repo_slug("My Cool Pod!!") == "My-Cool-Pod"
    assert _github_repo_slug("  ") == "lemma-pod"
    assert _github_repo_slug("already-safe_name.v2") == "already-safe_name.v2"


def test_repo_slug_truncates_to_github_max_length():
    assert len(_github_repo_slug("x" * 200)) == 100


def _zip_with(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return buffer.getvalue()


def test_capability_bullets_count_resource_dirs_tolerating_a_wrapper_folder():
    archive = _zip_with(
        {
            "trumpet/tables/widgets/widgets.json": b"{}",
            "trumpet/tables/gizmos/gizmos.json": b"{}",
            "trumpet/agents/greeter/greeter.json": b"{}",
        }
    )
    bullets = _capability_bullets(archive)
    assert "- 2 tables" in bullets
    assert "- 1 agent" in bullets


def test_archive_entries_yields_every_file_not_directories():
    archive = _zip_with(
        {
            "trumpet/pod.json": b"{}",
            "trumpet/tables/widgets/widgets.json": b"{}",
        }
    )
    entries = dict(_archive_entries(archive))
    assert entries == {
        "trumpet/pod.json": b"{}",
        "trumpet/tables/widgets/widgets.json": b"{}",
    }


def test_strip_bundle_root_drops_the_pod_name_wrapper():
    entries = [
        ("trumpet/pod.json", b"{}"),
        ("trumpet/tables/widgets/widgets.json", b"{}"),
    ]
    assert _strip_bundle_root("trumpet", entries) == [
        ("pod.json", b"{}"),
        ("tables/widgets/widgets.json", b"{}"),
    ]


def test_strip_bundle_root_leaves_a_path_without_the_wrapper_alone():
    # Defensive: a path that doesn't start with "<pod_name>/" is passed through
    # untouched rather than mangled.
    assert _strip_bundle_root("trumpet", [("pod.json", b"{}")]) == [("pod.json", b"{}")]


def test_capability_bullets_empty_bundle_yields_no_bullets():
    archive = _zip_with({"trumpet/pod.json": b"{}"})
    assert _capability_bullets(archive) == []


def test_render_readme_includes_name_badge_and_bullets():
    archive = _zip_with({"tables/widgets/widgets.json": b"{}"})
    readme = _render_readme("Trumpet", "A horn pod.", archive, "https://lemma.work/import/github/a/b")
    assert "# Trumpet" in readme
    assert "A horn pod." in readme
    assert "[![Import to Lemma]" in readme
    assert "https://lemma.work/import/github/a/b" in readme
    assert "- 1 table" in readme


def test_extract_result_unwraps_a_data_envelope():
    assert _extract_result({"data": {"full_name": "a/b"}}) == {"full_name": "a/b"}


def test_extract_result_accepts_a_raw_dict():
    assert _extract_result({"full_name": "a/b"}) == {"full_name": "a/b"}


def test_is_payload_too_large_detects_413():
    assert _is_payload_too_large(Exception("Error code: 413 - {'error': 'Request Entity Too Large'}"))
    assert not _is_payload_too_large(Exception("unauthorized"))


class FakeFileUseCases:
    """Fakes execute_operation_for_auth_config for file pushes: raises a
    413-shaped error for any path in ``too_large``, records every other push."""

    def __init__(self, too_large: set[str]):
        self.too_large = too_large
        self.pushed: list[str] = []

    async def execute_operation_for_auth_config(self, *, operation_name, payload, **_kwargs):
        path = payload["path"]
        if path in self.too_large:
            raise OperationExecutionError(f"Error code: 413 - Request Entity Too Large ({path})")
        self.pushed.append(path)


@pytest.mark.asyncio
async def test_push_files_best_effort_skips_oversized_files_and_keeps_going():
    use_cases = FakeFileUseCases(too_large={"data.json"})
    files = [("README.md", b"hi"), ("pod.json", b"{}"), ("data.json", b"x" * 999)]
    skipped = await _push_files_best_effort(use_cases, {}, "owner", "repo", files)
    assert skipped == ["data.json"]
    assert use_cases.pushed == ["README.md", "pod.json"]


@pytest.mark.asyncio
async def test_push_files_best_effort_does_not_swallow_other_errors():
    class FailAuth(FakeFileUseCases):
        async def execute_operation_for_auth_config(self, *, operation_name, payload, **_kwargs):
            raise OperationExecutionError("unauthorized")

    use_cases = FailAuth(too_large=set())
    with pytest.raises(OperationExecutionError, match="unauthorized"):
        await _push_files_best_effort(use_cases, {}, "owner", "repo", [("pod.json", b"{}")])


class FakeChunkingRepo:
    """A fake that rejects any push above ``ceiling`` bytes (of the base64
    content, mimicking Composio's real request-size limit) and otherwise
    stores the file — enough to exercise adaptive chunk-size shrinking and
    prove a real repo round-trip through _reassemble_chunked_entries."""

    def __init__(self, ceiling: int):
        self.ceiling = ceiling
        self.files: dict[str, bytes] = {}

    async def execute_operation_for_auth_config(self, *, operation_name, payload, **_kwargs):
        content_b64 = payload["content"]
        if len(content_b64) > self.ceiling:
            raise OperationExecutionError("Error code: 413 - Request Entity Too Large")
        self.files[payload["path"]] = base64.b64decode(content_b64)


@pytest.mark.asyncio
async def test_push_one_file_adaptively_shrinks_chunk_size_until_it_fits():
    # A ceiling well below the first chunk-size guess (150_000) forces at
    # least one halving before pieces are small enough to land.
    repo = FakeChunkingRepo(ceiling=60_000)
    content = b"a" * 200_000
    ok = await _push_files_best_effort(repo, {}, "owner", "repo", [("dist.zip", content)])
    assert ok == []  # nothing skipped -- it found a chunk size that fit
    assert "dist.zip" not in repo.files  # pushed as pieces, not the whole file
    assert len(repo.files) > 1


@pytest.mark.asyncio
async def test_chunked_publish_reassembles_byte_identical_on_import():
    repo = FakeChunkingRepo(ceiling=60_000)
    original = bytes((i * 7) % 256 for i in range(200_000))  # non-repeating content
    await _push_files_best_effort(repo, {}, "owner", "repo", [("apps/mini/dist.zip", original)])

    archive = _zip_with(repo.files)
    reassembled = _archive_entries(_reassemble_chunked_entries(archive))
    assert dict(reassembled) == {"apps/mini/dist.zip": original}


def test_reassemble_drops_an_incomplete_chunk_set():
    # Only 2 of 3 chunks present (e.g. a chunk-size shrink left a stale partial
    # set behind) -- must not silently reassemble corrupt/truncated content.
    archive = _zip_with(
        {
            "dist.zip.chunk0000of0003": b"AAA",
            "dist.zip.chunk0001of0003": b"BBB",
            "pod.json": b"{}",
        }
    )
    entries = dict(_archive_entries(_reassemble_chunked_entries(archive)))
    assert "dist.zip" not in entries
    assert entries["pod.json"] == b"{}"
