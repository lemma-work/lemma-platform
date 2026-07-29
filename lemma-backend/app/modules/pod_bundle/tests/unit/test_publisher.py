"""Atomic GitHub publisher, response contracts, and README safety."""

import base64
from pathlib import Path

import pytest

from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationNotFoundError,
)
from app.modules.pod_bundle.domain.errors import (
    GithubBranchRaceError,
    GithubPublishCapabilityUnavailableError,
    GithubRepositoryExistsError,
    GithubRepositoryNotFoundError,
)
from app.modules.pod_bundle.domain.state import PublishMode
from app.modules.pod_bundle.infrastructure.ai_readme import polish_readme
from app.modules.pod_bundle.infrastructure.github_publisher import (
    ComposioGithubOps,
    GithubPublisher,
    RepoCreateResult,
)
from app.modules.pod_bundle.infrastructure.publish_manifest import (
    PUBLISH_MANIFEST_PATH,
    parse_publish_manifest,
    prepare_published_bundle,
)
from app.modules.pod_bundle.infrastructure.readme import (
    install_badge,
    install_badge_url,
    install_target,
    render_readme,
)
from app.modules.pod_bundle.infrastructure.social_card import render_social_card


class FakeOps:
    def __init__(self):
        self.create_calls = 0
        self.commits: list[dict] = []
        self.repo: RepoCreateResult | None = None
        self.content: dict[str, bytes] = {}
        self.head = "head-0"
        self.race = False
        self.ambiguous_commit = False

    async def resolve_repo(self, *, name):
        return self.repo

    async def create_repo(self, *, name, private, description):
        del description
        self.create_calls += 1
        self.repo = RepoCreateResult(
            owner="acme",
            repo=name,
            html_url=f"https://github.com/acme/{name}",
            default_branch="main",
            private=private,
        )
        self.content["README.md"] = f"# {name}".encode()
        return self.repo

    async def get_head(self, *, owner, repo, branch):
        del owner, repo, branch
        return self.head

    async def get_file(self, *, owner, repo, path, ref=None):
        del owner, repo, ref
        return self.content.get(path)

    async def commit_files(
        self,
        *,
        owner,
        repo,
        branch,
        upserts,
        deletes,
        message,
        expected_head,
        prefer_multi_file,
    ):
        del owner, repo, branch, message
        if self.race or expected_head != self.head:
            raise GithubBranchRaceError()
        self.commits.append(
            {
                "upserts": dict(upserts),
                "deletes": set(deletes),
                "prefer_multi_file": prefer_multi_file,
            }
        )
        self.content.update(upserts)
        for path in deletes:
            self.content.pop(path, None)
        self.head = f"head-{len(self.commits)}"
        if self.ambiguous_commit:
            self.ambiguous_commit = False
            raise ConnectionError("response lost")
        return self.head


def _repo(ops: FakeOps, name: str = "crm") -> RepoCreateResult:
    ops.repo = RepoCreateResult(
        owner="acme",
        repo=name,
        html_url=f"https://github.com/acme/{name}",
        default_branch="main",
    )
    return ops.repo


async def _publish(
    ops: FakeOps,
    *,
    publish_id: str = "pub-1",
    mode: PublishMode = PublishMode.CREATE,
    files: dict[str, bytes] | None = None,
    already_created: RepoCreateResult | None = None,
):
    return await GithubPublisher(ops).publish(
        publish_id=publish_id,
        mode=mode,
        repo_name="crm",
        private=False,
        description="CRM",
        files=files or {"pod.json": b"{}"},
        readme="# CRM\n",
        already_created=already_created,
    )


# --- readme ------------------------------------------------------------------


def test_render_readme_has_badge_counts_and_escaped_user_content():
    rendered = render_readme(
        pod_name='CRM <script>',
        description="[Leads](javascript:alert(1))",
        resource_counts={"tables": 2, "agents": 1, "functions": 0},
        owner="acme",
        repo="crm",
        icon_url='" onerror="alert(1)',
    )
    assert install_badge("acme", "crm") in rendered.splitlines()
    assert 'src="./social-card.png"' in rendered
    assert "**Tables** | 2 |" in rendered and "**Agents** | 1 |" in rendered
    assert "Functions" not in rendered
    assert "<script>" not in rendered
    assert "](javascript:" not in rendered
    assert "onerror" not in rendered


def test_render_readme_default_tagline_and_install_button():
    rendered = render_readme(
        pod_name="CRM",
        description=None,
        resource_counts={},
        owner="acme",
        repo="crm",
    )
    assert "ready to run with your team" in rendered
    badge = install_badge("acme", "crm")
    assert "/import/github/acme/crm" in badge
    assert badge == (
        f'<a href="{install_target("acme", "crm")}">'
        f'<img src="{install_badge_url()}" height="44" '
        'alt="Run it on Lemma" /></a>'
    )


def test_social_card_is_a_full_size_png():
    card = render_social_card(
        pod_name="Research Desk",
        source_label="github.com/acme/research-desk",
    )
    assert card.startswith(b"\x89PNG\r\n\x1a\n")
    assert int.from_bytes(card[16:20], "big") == 1200
    assert int.from_bytes(card[20:24], "big") == 630


# --- publisher ---------------------------------------------------------------


async def test_create_publishes_all_managed_files_in_one_atomic_commit():
    ops = FakeOps()
    repo = await _publish(
        ops,
        files={"pod.json": b"{}", "tables/leads/leads.json": b"{}"},
    )
    assert repo.html_url.endswith("/acme/crm")
    assert ops.create_calls == 1
    assert len(ops.commits) == 1
    commit = ops.commits[0]
    assert commit["prefer_multi_file"] is True
    assert {
        "README.md",
        "pod.json",
        "tables/leads/leads.json",
        PUBLISH_MANIFEST_PATH,
    } <= set(commit["upserts"])
    manifest = parse_publish_manifest(commit["upserts"][PUBLISH_MANIFEST_PATH])
    assert manifest["publish_id"] == "pub-1"


async def test_create_conflicts_on_existing_repo_and_update_requires_one():
    ops = FakeOps()
    _repo(ops)
    with pytest.raises(GithubRepositoryExistsError):
        await GithubPublisher(ops).create_repo(
            repo_name="crm",
            private=False,
            description=None,
            mode=PublishMode.CREATE,
        )

    missing = FakeOps()
    with pytest.raises(GithubRepositoryNotFoundError):
        await GithubPublisher(missing).create_repo(
            repo_name="crm",
            private=False,
            description=None,
            mode=PublishMode.UPDATE,
        )


async def test_update_preserves_unrelated_files_and_deletes_only_stale_managed_paths():
    ops = FakeOps()
    repo = await _publish(
        ops,
        files={"pod.json": b"{}", "tables/old/old.json": b"old"},
    )
    ops.content["notes/keep.md"] = b"human content"

    await _publish(
        ops,
        publish_id="pub-2",
        mode=PublishMode.UPDATE,
        files={"pod.json": b'{"name":"new"}'},
        already_created=repo,
    )

    assert ops.content["notes/keep.md"] == b"human content"
    assert "tables/old/old.json" not in ops.content
    assert ops.content["pod.json"] == b'{"name":"new"}'
    assert ops.commits[-1]["prefer_multi_file"] is False


async def test_first_legacy_update_overwrites_large_logical_path_without_deleting():
    ops = FakeOps()
    repo = _repo(ops)
    large = bytes(range(256)) * 1_600
    ops.content["apps/x/dist.zip"] = b"legacy"

    await _publish(
        ops,
        publish_id="pub-legacy-update",
        mode=PublishMode.UPDATE,
        files={"apps/x/dist.zip": large},
        already_created=repo,
    )

    assert ops.commits[-1]["deletes"] == set()
    assert ops.content["apps/x/dist.zip"] == large
    assert not any(".chunk" in path for path in ops.content)


async def test_large_file_roundtrips_from_chunks(tmp_path: Path):
    ops = FakeOps()
    large = bytes(range(256)) * 1_600
    await _publish(ops, files={"apps/x/dist.zip": large})

    chunk_paths = sorted(path for path in ops.content if ".chunk" in path)
    assert len(chunk_paths) == 3
    for path, content in ops.content.items():
        destination = tmp_path / path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(content)

    assert prepare_published_bundle(tmp_path) is True
    assert (tmp_path / "apps/x/dist.zip").read_bytes() == large
    assert not any((tmp_path / path).exists() for path in chunk_paths)


async def test_update_rejects_branch_race():
    ops = FakeOps()
    repo = _repo(ops)
    ops.race = True
    with pytest.raises(GithubBranchRaceError):
        await _publish(
            ops,
            mode=PublishMode.UPDATE,
            already_created=repo,
        )


async def test_response_lost_retry_uses_manifest_checkpoint():
    ops = FakeOps()
    ops.ambiguous_commit = True
    repo = await GithubPublisher(ops).create_repo(
        repo_name="crm",
        private=False,
        description=None,
    )
    with pytest.raises(ConnectionError):
        await _publish(ops, already_created=repo)
    assert len(ops.commits) == 1

    await _publish(ops, already_created=repo)
    assert len(ops.commits) == 1


async def test_provider_error_after_create_resolves_accepted_repository():
    class AmbiguousCreateOps(FakeOps):
        async def create_repo(self, *, name, private, description):
            await super().create_repo(
                name=name,
                private=private,
                description=description,
            )
            raise OperationExecutionInfrastructureError("response lost")

    ops = AmbiguousCreateOps()
    repo = await GithubPublisher(ops).create_repo(
        repo_name="crm",
        private=False,
        description=None,
    )
    assert repo.repo == "crm"
    assert ops.create_calls == 1


async def test_composio_ops_normalizes_live_content_response_shape():
    calls = []

    async def runner(op, payload):
        calls.append((op, payload))
        if op == "GITHUB_CREATE_A_REPOSITORY_FOR_THE_AUTHENTICATED_USER":
            data = {
                "full_name": "acme/crm",
                "html_url": "https://github.com/acme/crm",
                "default_branch": "main",
            }
        elif op == "GITHUB_GET_REPOSITORY_CONTENT":
            data = {
                "content": {
                    "type": "file",
                    "sha": "blob-sha",
                    "content": base64.b64encode(b"manifest").decode(),
                }
            }
        else:
            data = {}
        return {"result": {"data": data, "successful": True}}

    ops = ComposioGithubOps(runner)
    repo = await ops.create_repo(name="crm", private=False, description="d")
    content = await ops.get_file(
        owner="acme",
        repo="crm",
        path=PUBLISH_MANIFEST_PATH,
    )
    assert repo.owner == "acme" and repo.default_branch == "main"
    assert content == b"manifest"
    assert calls[0][1]["auto_init"] is True


async def test_composio_ops_normalizes_live_user_and_repository_shapes():
    async def runner(op, payload):
        if op == "GITHUB_GET_THE_AUTHENTICATED_USER":
            data = {"login": "acme", "id": 42}
        else:
            assert payload == {"owner": "acme", "repo": "crm"}
            data = {
                "id": 99,
                "full_name": "acme/crm",
                "html_url": "https://github.com/acme/crm",
                "default_branch": "trunk",
                "owner": {"login": "acme"},
                "private": True,
            }
        return {"result": {"data": data, "successful": True}, "metadata": {}}

    repo = await ComposioGithubOps(runner).resolve_repo(name="crm")
    assert repo is not None
    assert (repo.owner, repo.repo, repo.default_branch, repo.private) == (
        "acme",
        "crm",
        "trunk",
        True,
    )


async def test_composio_commit_multiple_uses_one_non_forced_operation():
    calls = []

    async def runner(op, payload):
        calls.append((op, payload))
        return {
            "result": {
                "data": {"new_commit_sha": "commit-sha"},
                "successful": True,
            }
        }

    sha = await ComposioGithubOps(runner).commit_files(
        owner="acme",
        repo="crm",
        branch="main",
        upserts={"pod.json": b"{}"},
        deletes=set(),
        message="publish",
        expected_head="head",
        prefer_multi_file=True,
    )
    assert sha == "commit-sha"
    assert calls == [
        (
            "GITHUB_COMMIT_MULTIPLE_FILES",
            {
                "owner": "acme",
                "repo": "crm",
                "branch": "main",
                "message": "publish",
                "force": False,
                "max_retries": 0,
                "upserts": [
                    {
                        "path": "pod.json",
                        "content": "e30=",
                        "encoding": "base64",
                    }
                ],
                "deletes": [],
            },
        )
    ]


async def test_composio_git_data_commit_uses_live_ref_commit_and_tree_shapes():
    calls = []

    async def runner(op, payload):
        calls.append((op, payload))
        data = {
            "GITHUB_GET_A_REFERENCE": {"object": {"sha": "head-sha"}},
            "GITHUB_GET_A_COMMIT": {"commit": {"tree": {"sha": "base-tree"}}},
            "GITHUB_CREATE_A_BLOB": {"sha": "blob-sha"},
            "GITHUB_CREATE_A_TREE": {"sha": "new-tree"},
            "GITHUB_CREATE_A_COMMIT": {"sha": "new-commit"},
            "GITHUB_UPDATE_A_REFERENCE": {"object": {"sha": "new-commit"}},
        }[op]
        return {"result": {"data": data, "successful": True}}

    sha = await ComposioGithubOps(runner).commit_files(
        owner="acme",
        repo="crm",
        branch="main",
        upserts={"pod.json": b"{}"},
        deletes={"old.json"},
        message="publish",
        expected_head="head-sha",
        prefer_multi_file=False,
    )
    assert sha == "new-commit"
    tree_call = next(payload for op, payload in calls if op == "GITHUB_CREATE_A_TREE")
    assert tree_call["base_tree"] == "base-tree"
    assert {"path": "old.json", "mode": "100644", "type": "blob", "sha": None} in tree_call[
        "tree"
    ]
    update_call = next(
        payload for op, payload in calls if op == "GITHUB_UPDATE_A_REFERENCE"
    )
    assert update_call["force"] is False


async def test_missing_atomic_connector_operation_is_a_stable_capability_error():
    async def runner(op, payload):
        del payload
        raise OperationNotFoundError(op)

    with pytest.raises(GithubPublishCapabilityUnavailableError) as exc:
        await ComposioGithubOps(runner).get_head(
            owner="acme",
            repo="crm",
            branch="main",
        )
    assert exc.value.code == "GITHUB_PUBLISH_CAPABILITY_UNAVAILABLE"
    assert exc.value.details == {"operation": "GITHUB_GET_A_REFERENCE"}


# --- AI polish ---------------------------------------------------------------


async def test_polish_degrades_on_error_or_missing_invariants():
    original = render_readme(
        pod_name="CRM",
        description="Leads",
        resource_counts={"tables": 2},
        owner="acme",
        repo="crm",
    )

    async def boom(_):
        raise RuntimeError("model down")

    async def strip_structure(_):
        return "# Polished\nimg.shields.io"

    assert await polish_readme(original, polish_fn=boom) == original
    assert await polish_readme(original, polish_fn=strip_structure) == original

    async def break_centering(text):
        return text.replace("</div>", "", 1)

    assert await polish_readme(original, polish_fn=break_centering) == original


async def test_polish_accepts_fenced_output_preserving_every_invariant():
    original = render_readme(
        pod_name="CRM",
        description="Leads",
        resource_counts={"tables": 2},
        owner="acme",
        repo="crm",
    )

    async def fenced(text):
        return f"```markdown\n{text}\n\nPolished copy.\n```"

    out = await polish_readme(original, polish_fn=fenced)
    assert out.endswith("Polished copy.")
