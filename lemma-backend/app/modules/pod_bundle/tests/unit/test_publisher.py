"""Atomic GitHub publisher, response contracts, and README safety."""

import base64
from pathlib import Path

import pytest

from app.modules.connectors.domain.errors import (
    OperationExecutionInfrastructureError,
    OperationExecutionNotFoundError,
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
from app.modules.pod_bundle.infrastructure.github_ops import (
    NativeGithubOps,
)
from app.modules.pod_bundle.infrastructure.github_publisher import (
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

    async def resolve_repo(self, *, name, owner=None):
        del owner
        del name
        return self.repo

    def exists(self, name: str = "crm", *, private: bool = False) -> RepoCreateResult:
        """The repository the person made on GitHub before publishing.

        Lemma no longer creates one: `POST /user/repos` needs the OAuth `repo`
        scope, App user tokens carry none, and GitHub refuses the endpoint to
        installations outright -- so neither identity the App has can call it.
        """
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
        # None is a repository with no commits yet -- "New repository" without
        # "Add a README", which is the normal starting point now that Lemma
        # does not create one.
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
    ):
        del owner, repo, branch, message
        if self.race or expected_head != self.head:
            raise GithubBranchRaceError()
        self.commits.append(
            {
                "upserts": dict(upserts),
                "deletes": set(deletes),
                "expected_head": expected_head,
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
    return ops.exists(name)


async def _publish(
    ops: FakeOps,
    *,
    publish_id: str = "pub-1",
    mode: PublishMode = PublishMode.CREATE,
    files: dict[str, bytes] | None = None,
    already_created: RepoCreateResult | None = None,
):
    # The repository exists before a publish now, always: the person makes it
    # on GitHub and Lemma publishes into it, because a GitHub App cannot create
    # one on their behalf.
    if already_created is None and ops.repo is None:
        ops.exists()
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
        pod_name="CRM <script>",
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
    assert len(ops.commits) == 1
    commit = ops.commits[0]
    assert {
        "README.md",
        "pod.json",
        "tables/leads/leads.json",
        PUBLISH_MANIFEST_PATH,
    } <= set(commit["upserts"])
    manifest = parse_publish_manifest(commit["upserts"][PUBLISH_MANIFEST_PATH])
    assert manifest["publish_id"] == "pub-1"


async def test_create_refuses_a_repository_lemma_has_already_published_to():
    """`CREATE` still means "not over the top of an existing pod". What it
    refuses is now the manifest rather than the repository: an empty repository
    the person just made is the normal starting point."""
    ops = FakeOps()
    await _publish(ops, already_created=ops.exists())

    with pytest.raises(GithubRepositoryExistsError):
        await GithubPublisher(ops).resolve_target(
            repo_name="crm",
            private=False,
            description=None,
            mode=PublishMode.CREATE,
        )


async def test_an_empty_repository_is_what_create_is_for():
    ops = FakeOps()
    repo = ops.exists()

    resolved = await GithubPublisher(ops).resolve_target(
        repo_name="crm", private=False, description=None, mode=PublishMode.CREATE
    )

    assert resolved is repo


async def test_a_repository_we_cannot_reach_says_what_to_do_about_it():
    """Absent and not-installed are the same 404 from GitHub, so the message
    names both rather than guessing which one happened."""
    missing = FakeOps()
    for mode in (PublishMode.CREATE, PublishMode.UPDATE):
        with pytest.raises(GithubRepositoryNotFoundError) as raised:
            await GithubPublisher(missing).resolve_target(
                repo_name="crm",
                private=False,
                description=None,
                mode=mode,
            )
        assert "github.com/new" in str(raised.value)


async def test_an_organisation_repository_is_resolved_under_its_owner():
    """Publishing used to resolve the connected user's own login and nothing
    else, so a pod could only ever land in a personal namespace."""
    seen: dict[str, object] = {}

    class OwnerAwareOps(FakeOps):
        async def resolve_repo(self, *, name, owner=None):
            seen["name"] = name
            seen["owner"] = owner
            return self.repo

    ops = OwnerAwareOps()
    ops.exists()
    await GithubPublisher(ops).resolve_target(
        repo_name="acme-corp/crm", private=False, description=None
    )

    assert seen == {"name": "crm", "owner": "acme-corp"}


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
    repo = ops.exists()
    with pytest.raises(ConnectionError):
        await _publish(ops, already_created=repo)
    assert len(ops.commits) == 1

    await _publish(ops, already_created=repo)
    assert len(ops.commits) == 1


async def test_native_ops_reads_github_repository_and_content_shapes():
    calls = []

    async def runner(op, payload):
        calls.append((op, payload))
        if op == "users_get_authenticated":
            result = {"login": "acme"}
        elif op == "repos_get":
            result = {
                "id": 1,
                "full_name": "acme/crm",
                "html_url": "https://github.com/acme/crm",
                "default_branch": "main",
            }
        elif op == "repos_get_content":
            # GitHub answers with the file object itself: base64 at the top
            # level, not wrapped in a broker's envelope.
            result = {
                "type": "file",
                "sha": "blob-sha",
                "encoding": "base64",
                "content": base64.b64encode(b"manifest").decode(),
            }
        else:
            result = {}
        return {"result": result}

    ops = NativeGithubOps(runner)
    repo = await ops.resolve_repo(name="crm")
    content = await ops.get_file(
        owner="acme",
        repo="crm",
        path=PUBLISH_MANIFEST_PATH,
    )
    assert repo.owner == "acme" and repo.default_branch == "main"
    assert content == b"manifest"
    # The owner comes from the connected account when the caller names none, so
    # a bare repository name still resolves.
    assert calls[0][0] == "users_get_authenticated"
    assert calls[1] == ("repos_get", {"owner": "acme", "repo": "crm"})


async def test_native_ops_resolves_repo_through_the_authenticated_user():
    async def runner(op, payload):
        if op == "users_get_authenticated":
            result = {"login": "acme", "id": 42}
        else:
            assert op == "repos_get"
            assert payload == {"owner": "acme", "repo": "crm"}
            result = {
                "id": 99,
                "full_name": "acme/crm",
                "html_url": "https://github.com/acme/crm",
                "default_branch": "trunk",
                "owner": {"login": "acme"},
                "private": True,
            }
        return {"result": result}

    repo = await NativeGithubOps(runner).resolve_repo(name="crm")
    assert repo is not None
    assert (repo.owner, repo.repo, repo.default_branch, repo.private) == (
        "acme",
        "crm",
        "trunk",
        True,
    )


async def test_native_ops_reads_a_missing_repository_as_not_yet_created():
    async def runner(op, payload):
        del payload
        if op == "users_get_authenticated":
            return {"result": {"login": "acme"}}
        raise OperationExecutionNotFoundError("repos_get")

    assert await NativeGithubOps(runner).resolve_repo(name="crm") is None


async def test_native_commit_builds_one_commit_from_git_data_operations():
    calls = []

    async def runner(op, payload):
        calls.append((op, payload))
        result = {
            "git_get_ref": {"object": {"sha": "head-sha"}},
            "repos_get_commit": {"commit": {"tree": {"sha": "base-tree"}}},
            "git_create_blob": {"sha": "blob-sha"},
            "git_create_tree": {"sha": "new-tree"},
            "git_create_commit": {"sha": "new-commit"},
            "git_update_ref": {"object": {"sha": "new-commit"}},
        }[op]
        return {"result": result}

    sha = await NativeGithubOps(runner).commit_files(
        owner="acme",
        repo="crm",
        branch="main",
        upserts={"pod.json": b"{}"},
        deletes={"old.json"},
        message="publish",
        expected_head="head-sha",
    )

    assert sha == "new-commit"
    assert [op for op, _ in calls] == [
        "git_get_ref",
        "repos_get_commit",
        "git_create_blob",
        "git_create_tree",
        "git_create_commit",
        "git_update_ref",
    ]
    blob_call = next(payload for op, payload in calls if op == "git_create_blob")
    assert blob_call["body"] == {"content": "e30=", "encoding": "base64"}
    tree_call = next(payload for op, payload in calls if op == "git_create_tree")
    assert tree_call["body"]["base_tree"] == "base-tree"
    # A deletion is a tree entry with a null sha, not a separate call.
    assert {
        "path": "old.json",
        "mode": "100644",
        "type": "blob",
        "sha": None,
    } in tree_call["body"]["tree"]
    commit_call = next(payload for op, payload in calls if op == "git_create_commit")
    assert commit_call["body"]["parents"] == ["head-sha"]
    update_call = next(payload for op, payload in calls if op == "git_update_ref")
    # The whole ref path, not just the branch: the operation keeps its slash.
    assert update_call["ref"] == "heads/main"
    assert update_call["body"] == {"sha": "new-commit", "force": False}


async def test_native_commit_reads_a_rejected_ref_update_as_a_branch_race():
    async def runner(op, payload):
        del payload
        if op == "git_update_ref":
            raise OperationExecutionInfrastructureError(
                "rejected", details={"upstream_status": 422}
            )
        return {
            "result": {
                "git_get_ref": {"object": {"sha": "head-sha"}},
                "repos_get_commit": {"commit": {"tree": {"sha": "base-tree"}}},
                "git_create_blob": {"sha": "blob-sha"},
                "git_create_tree": {"sha": "new-tree"},
                "git_create_commit": {"sha": "new-commit"},
            }[op]
        }

    with pytest.raises(GithubBranchRaceError):
        await NativeGithubOps(runner).commit_files(
            owner="acme",
            repo="crm",
            branch="main",
            upserts={"pod.json": b"{}"},
            deletes=set(),
            message="publish",
            expected_head="head-sha",
        )


async def test_missing_atomic_connector_operation_is_a_stable_capability_error():
    async def runner(op, payload):
        del payload
        raise OperationNotFoundError(op)

    with pytest.raises(GithubPublishCapabilityUnavailableError) as exc:
        await NativeGithubOps(runner).get_head(
            owner="acme",
            repo="crm",
            branch="main",
        )
    assert exc.value.code == "GITHUB_PUBLISH_CAPABILITY_UNAVAILABLE"
    assert exc.value.details == {"operation": "git_get_ref"}


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


class TestAnEmptyRepository:
    """A repository made without a README has no branch and no ref.

    Lemma no longer creates the repository, so this is the ordinary starting
    point rather than an edge case -- and publishing into it has to be the
    initial commit: no parent, no base tree, and a ref that is *created* rather
    than updated, because PATCH on a missing ref is a 422.
    """

    async def test_create_publishes_into_a_repository_with_no_commits(self):
        ops = FakeOps()
        repo = ops.exists()
        ops.head = None
        ops.content.clear()

        await _publish(ops, already_created=repo)

        assert len(ops.commits) == 1
        commit = ops.commits[0]
        assert commit["expected_head"] is None
        assert PUBLISH_MANIFEST_PATH in commit["upserts"]

    async def test_the_native_ops_write_an_initial_commit_and_create_the_ref(self):
        """What actually reaches GitHub for a repository with no commits.

        The fake above proves the publisher's decision; this proves the request
        shapes, which is where an empty repository actually failed: a commit
        carrying a parent that does not exist, a tree based on nothing, and a
        PATCH to a ref that is not there yet.
        """
        calls: list[tuple[str, dict]] = []

        async def runner(op, payload):
            calls.append((op, payload))
            if op == "git_get_ref":
                raise OperationExecutionNotFoundError("no ref yet")
            if op == "git_create_blob":
                return {"result": {"sha": "blob-1"}}
            if op == "git_create_tree":
                return {"result": {"sha": "tree-1"}}
            if op == "git_create_commit":
                return {"result": {"sha": "commit-1"}}
            return {"result": {}}

        ops = NativeGithubOps(runner)
        assert await ops.get_head(owner="acme", repo="crm", branch="main") is None

        sha = await ops.commit_files(
            owner="acme",
            repo="crm",
            branch="main",
            upserts={"pod.json": b"{}"},
            deletes=set(),
            message="init",
            expected_head=None,
        )

        assert sha == "commit-1"
        by_op = dict(calls)
        assert "git_get_commit" not in by_op, "asked for a commit that does not exist"
        assert "base_tree" not in by_op["git_create_tree"]["body"]
        assert by_op["git_create_commit"]["body"]["parents"] == []
        assert "git_update_ref" not in by_op, "PATCHed a ref that is not there yet"
        assert by_op["git_create_ref"]["body"] == {
            "ref": "refs/heads/main",
            "sha": "commit-1",
        }

    async def test_an_empty_repository_is_not_one_lemma_published_to(self):
        """`CREATE` refuses a repository carrying a manifest. An empty one
        carries nothing, so it must not be refused."""
        ops = FakeOps()
        ops.exists()
        ops.head = None
        ops.content.clear()

        resolved = await GithubPublisher(ops).resolve_target(
            repo_name="crm", private=False, description=None, mode=PublishMode.CREATE
        )

        assert resolved is ops.repo
