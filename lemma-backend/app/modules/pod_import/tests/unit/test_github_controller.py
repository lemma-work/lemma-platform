"""Unit tests for the pure helpers behind GitHub publish/import — the parts
verifiable without a live Composio/GitHub connection."""

from __future__ import annotations

import base64
import io
import json
import zipfile
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.modules.connectors.domain.errors import (
    OperationExecutionError,
    OperationExecutionUnauthorizedError,
)
from app.modules.pod_import.api.controllers.github_controller import (
    GithubPublishRequest,
    _archive_entries,
    _create_repo_with_retry,
    _extract_result,
    _github_repo_slug,
    _is_payload_too_large,
    _push_one_file_best_effort,
    _reassemble_chunked_entries,
    _strip_bundle_root,
    preview_github_publish,
    publish_pod_to_github,
)
from app.modules.pod_import.infrastructure.readme import IMPORT_BADGE_URL


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


async def _push_files(use_cases, files: list[tuple[str, bytes]]) -> list[str]:
    """The publish loop's per-file skeleton (publish itself streams a progress
    line between pushes, so there is no batch helper to import)."""
    skipped: list[str] = []
    for path, content in files:
        if not await _push_one_file_best_effort(use_cases, {}, "owner", "repo", path, content):
            skipped.append(path)
    return skipped


@pytest.mark.asyncio
async def test_push_files_skips_oversized_files_and_keeps_going():
    use_cases = FakeFileUseCases(too_large={"data.json"})
    files = [("README.md", b"hi"), ("pod.json", b"{}"), ("data.json", b"x" * 999)]
    skipped = await _push_files(use_cases, files)
    assert skipped == ["data.json"]
    assert use_cases.pushed == ["README.md", "pod.json"]


@pytest.mark.asyncio
async def test_push_one_file_does_not_swallow_other_errors():
    class FailAuth(FakeFileUseCases):
        async def execute_operation_for_auth_config(self, *, operation_name, payload, **_kwargs):
            raise OperationExecutionError("unauthorized")

    use_cases = FailAuth(too_large=set())
    with pytest.raises(OperationExecutionError, match="unauthorized"):
        await _push_files(use_cases, [("pod.json", b"{}")])


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
    skipped = await _push_files(repo, [("dist.zip", content)])
    assert skipped == []  # nothing skipped -- it found a chunk size that fit
    assert "dist.zip" not in repo.files  # pushed as pieces, not the whole file
    assert len(repo.files) > 1


@pytest.mark.asyncio
async def test_chunked_publish_reassembles_byte_identical_on_import():
    repo = FakeChunkingRepo(ceiling=60_000)
    original = bytes((i * 7) % 256 for i in range(200_000))  # non-repeating content
    await _push_files(repo, [("apps/mini/dist.zip", original)])

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


# -- publish NDJSON stream ----------------------------------------------------


class FakePublishUseCases:
    """One fake for the whole publish flow: "creates" the repo on the create
    operation (colliding for names in ``taken``, like FakeUseCases) and records
    every file push — content included, so a test can read back exactly what
    landed in README.md. ``fail_create`` injects the error a real Composio call
    would raise, to prove mid-stream errors become result lines rather than
    HTTP errors."""

    def __init__(self, fail_create: Exception | None = None, taken: set[str] | None = None):
        self.fail_create = fail_create
        self.taken = taken or set()
        self.pushed: list[str] = []
        self.files: dict[str, bytes] = {}

    async def execute_operation_for_auth_config(self, *, operation_name, payload, **_kwargs):
        if "CREATE_A_REPOSITORY" in operation_name:
            if self.fail_create is not None:
                raise self.fail_create
            if payload["name"] in self.taken:
                raise OperationExecutionError("name already exists on this account")
            return SimpleNamespace(result={"data": {"full_name": f"someone/{payload['name']}"}})
        self.pushed.append(payload["path"])
        self.files[payload["path"]] = base64.b64decode(payload["content"])


def _patch_publish_collaborators(monkeypatch, *, account, archive: bytes):
    from app.modules.pod_import.api.controllers import github_controller

    class FakeExporter:
        def __init__(self, uow):
            pass

        async def export(self, **_kwargs):
            return "trumpet", archive

    class FakeAccountRepo:
        def __init__(self, uow, encryption=None):
            pass

        async def get_by_user_org_and_app(self, *_args):
            return account

    monkeypatch.setattr(github_controller, "BundleExporter", FakeExporter)
    monkeypatch.setattr(github_controller, "AccountRepository", FakeAccountRepo)
    monkeypatch.setattr(github_controller, "get_secret_cipher", lambda: None)
    # Unit tests must never reach for a real model (a dev .env may configure one).
    monkeypatch.setattr(github_controller, "polish_available", lambda: False)
    monkeypatch.setattr(github_controller.settings, "frontend_url", "https://lemma.work/")


def _fake_pod_service():
    return SimpleNamespace(
        get_pod=lambda *_args: _async_return(
            SimpleNamespace(name="Trumpet", description="A horn pod.", organization_id=uuid4())
        )
    )


async def _publish_lines(use_cases, body: GithubPublishRequest | None = None) -> list[dict]:
    response = await publish_pod_to_github(
        pod_id=uuid4(),
        user=SimpleNamespace(id=uuid4()),
        pod_service=_fake_pod_service(),
        uow=None,
        ctx=None,
        use_cases=use_cases,
        request=None,
        body=body or GithubPublishRequest(),
    )
    assert response.media_type == "application/x-ndjson"
    body = "".join([chunk async for chunk in response.body_iterator])
    return [json.loads(line) for line in body.splitlines()]


def _async_return(value):
    async def _coro():
        return value

    return _coro()


@pytest.mark.asyncio
async def test_publish_streams_progress_then_a_published_result(monkeypatch):
    archive = _zip_with(
        {"trumpet/pod.json": b"{}", "trumpet/tables/widgets/widgets.json": b"{}"}
    )
    _patch_publish_collaborators(
        monkeypatch, account=SimpleNamespace(provider_account_id="someone"), archive=archive
    )
    use_cases = FakePublishUseCases()

    lines = await _publish_lines(use_cases)

    assert lines[0] == {"event": "progress", "stage": "export", "label": "Bundling pod"}
    assert {"event": "progress", "stage": "repo", "label": "Creating repository"} in lines
    assert {"event": "progress", "stage": "readme", "label": "Writing README"} in lines
    uploads = [line for line in lines if line.get("stage") == "upload"]
    # One upload line per file, emitted BEFORE the push, README first.
    assert [u["path"] for u in uploads] == [
        "README.md",
        "pod.json",
        "tables/widgets/widgets.json",
    ]
    assert [(u["done"], u["total"]) for u in uploads] == [(1, 3), (2, 3), (3, 3)]
    assert use_cases.pushed == ["README.md", "pod.json", "tables/widgets/widgets.json"]

    result = lines[-1]
    assert result["event"] == "result"
    assert result["status"] == "published"
    # The repo is named from the exported bundle's pod_name, not the display name.
    assert result["repo_url"] == "https://github.com/someone/trumpet"
    assert IMPORT_BADGE_URL in result["import_badge_markdown"]
    # Built from settings.frontend_url (trailing slash stripped), not hardcoded.
    assert result["import_badge_markdown"].endswith(
        "(https://lemma.work/import/github/someone/trumpet)"
    )
    # The result carries the exact README text that was committed.
    assert result["readme"] == use_cases.files["README.md"].decode("utf-8")
    assert result["readme"].startswith("# Trumpet")
    assert result["message"] is None


@pytest.mark.asyncio
async def test_publish_without_a_github_account_streams_one_not_connected_result(monkeypatch):
    _patch_publish_collaborators(monkeypatch, account=None, archive=_zip_with({}))

    lines = await _publish_lines(FakePublishUseCases())

    assert len(lines) == 1
    assert lines[0]["event"] == "result"
    assert lines[0]["status"] == "not_connected"
    assert lines[0]["readme"] is None
    assert "Connect GitHub" in lines[0]["message"]


@pytest.mark.asyncio
async def test_publish_maps_a_mid_stream_connector_error_to_a_failed_result(monkeypatch):
    _patch_publish_collaborators(
        monkeypatch,
        account=SimpleNamespace(provider_account_id="someone"),
        archive=_zip_with({"trumpet/pod.json": b"{}"}),
    )
    use_cases = FakePublishUseCases(fail_create=OperationExecutionError("rate limited"))

    lines = await _publish_lines(use_cases)

    assert lines[0]["stage"] == "export"  # progress had already streamed
    result = lines[-1]
    assert result["event"] == "result"
    assert result["status"] == "failed"
    assert result["readme"] is None
    assert result["message"] == "rate limited"


@pytest.mark.asyncio
async def test_publish_maps_an_unauthorized_error_to_a_not_connected_result(monkeypatch):
    _patch_publish_collaborators(
        monkeypatch,
        account=SimpleNamespace(provider_account_id="someone"),
        archive=_zip_with({"trumpet/pod.json": b"{}"}),
    )
    use_cases = FakePublishUseCases(
        fail_create=OperationExecutionUnauthorizedError("token expired")
    )

    lines = await _publish_lines(use_cases)

    result = lines[-1]
    assert result["status"] == "not_connected"
    assert "reconnected" in result["message"]


@pytest.mark.asyncio
async def test_preview_reports_non_zero_resource_counts(monkeypatch):
    archive = _zip_with(
        {
            "trumpet/pod.json": b'{"name": "trumpet"}',
            "trumpet/tables/widgets/widgets.json": b'{"name": "widgets"}',
            "trumpet/tables/gadgets/gadgets.json": b'{"name": "gadgets"}',
            "trumpet/agents/greeter/greeter.json": b'{"name": "greeter"}',
        }
    )
    _patch_publish_collaborators(
        monkeypatch, account=SimpleNamespace(provider_account_id="someone"), archive=archive
    )

    preview = await preview_github_publish(
        pod_id=uuid4(),
        user=SimpleNamespace(id=uuid4()),
        pod_service=_fake_pod_service(),
        uow=None,
        ctx=None,
        repo_name=None,
    )

    assert preview.repo_name == "trumpet"
    # Only kinds actually present in the bundle appear — no zero entries.
    assert preview.resource_counts == {"tables": 2, "agents": 1}


# -- README override ----------------------------------------------------------

_OVERRIDE_README = (
    "# My hand-written README\n\n"
    "Click [Import](https://lemma.work/import/github/someone/trumpet) to try it.\n\n"
    "Exactly my words, kept verbatim.\n"
)


@pytest.mark.asyncio
async def test_publish_readme_override_is_committed_with_the_import_url_rewritten(monkeypatch):
    _patch_publish_collaborators(
        monkeypatch,
        account=SimpleNamespace(provider_account_id="someone"),
        archive=_zip_with({"trumpet/pod.json": b"{}"}),
    )
    # "trumpet" is taken, so the repo lands on trumpet-2 — the override's
    # embedded import URL (guessed at preview time) must follow the rename,
    # while every other character stays exactly as the user wrote it.
    use_cases = FakePublishUseCases(taken={"trumpet"})

    lines = await _publish_lines(use_cases, body=GithubPublishRequest(readme=_OVERRIDE_README))

    committed = use_cases.files["README.md"].decode("utf-8")
    assert committed == _OVERRIDE_README.replace(
        "https://lemma.work/import/github/someone/trumpet",
        "https://lemma.work/import/github/someone/trumpet-2",
    )
    result = lines[-1]
    assert result["status"] == "published"
    assert result["repo_url"] == "https://github.com/someone/trumpet-2"
    # The result line carries the committed text (post-rewrite), not the input.
    assert result["readme"] == committed


@pytest.mark.asyncio
async def test_readme_override_rewrite_spares_other_links_and_punctuation(monkeypatch):
    _patch_publish_collaborators(
        monkeypatch,
        account=SimpleNamespace(provider_account_id="someone"),
        archive=_zip_with({"trumpet/pod.json": b"{}"}),
    )
    use_cases = FakePublishUseCases(taken={"trumpet"})
    override = (
        "Get it at https://lemma.work/import/github/someone/trumpet. Enjoy!\n"
        "Companion pod: https://lemma.work/import/github/alice/companion-pod\n"
    )

    await _publish_lines(use_cases, body=GithubPublishRequest(readme=override))

    committed = use_cases.files["README.md"].decode("utf-8")
    # Only this pod's link follows the collision rename; the sentence period
    # after it and the deliberate link to another pod stay byte-identical.
    assert "https://lemma.work/import/github/someone/trumpet-2. Enjoy!" in committed
    assert "https://lemma.work/import/github/alice/companion-pod" in committed


@pytest.mark.asyncio
async def test_publish_readme_override_never_consults_the_ai_polish(monkeypatch):
    from app.modules.pod_import.api.controllers import github_controller

    _patch_publish_collaborators(
        monkeypatch,
        account=SimpleNamespace(provider_account_id="someone"),
        archive=_zip_with({"trumpet/pod.json": b"{}"}),
    )

    def _must_not_be_called(*_args, **_kwargs):
        raise AssertionError("polish must not run for an override README")

    monkeypatch.setattr(github_controller, "polish_available", _must_not_be_called)
    monkeypatch.setattr(github_controller, "polish_readme", _must_not_be_called)

    lines = await _publish_lines(
        FakePublishUseCases(), body=GithubPublishRequest(readme="# Mine\n")
    )

    # An AssertionError inside the stream would surface as a failed result —
    # a published one proves neither polish function was touched.
    assert {"event": "progress", "stage": "readme", "label": "Writing README"} in lines
    assert lines[-1]["status"] == "published"
    assert lines[-1]["readme"] == "# Mine\n"
