"""Atomic GitHub publishing through Lemma's own GitHub connector.

Every call goes through the native (`http`-kind) GitHub connector's curated
operations, so publishing uses the same account, consent and token the agent's
`git`/`gh` use in a workspace -- there is no second GitHub identity behind a
broker.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Protocol

from app.modules.pod_bundle.domain.errors import (
    GithubRepositoryExistsError,
    GithubRepositoryNotFoundError,
)
from app.modules.pod_bundle.domain.state import PublishMode
from app.modules.pod_bundle.infrastructure.publish_manifest import (
    PUBLISH_MANIFEST_PATH,
    build_publish_layout,
    manifest_managed_paths,
    manifest_publish_id,
    parse_publish_manifest,
)

_BLOB_CONCURRENCY = 8


def _install_url() -> str | None:
    """Where the app's repository access is granted, if this deployment knows.

    Asked at the moment of failure rather than held as configuration, because
    it only ever appears in one sentence and only when that sentence is needed.
    """
    from app.modules.connectors.contracts.github import github_install_url

    return github_install_url()


class RepoCreateResult:
    def __init__(
        self,
        *,
        owner: str,
        repo: str,
        html_url: str,
        default_branch: str = "main",
        private: bool | None = None,
    ):
        self.owner = owner
        self.repo = repo
        self.html_url = html_url
        self.default_branch = default_branch or "main"
        self.private = private


class GithubOps(Protocol):
    async def resolve_repo(
        self, *, name: str, owner: str | None = None
    ) -> RepoCreateResult | None: ...

    async def get_head(self, *, owner: str, repo: str, branch: str) -> str | None: ...

    async def get_file(
        self,
        *,
        owner: str,
        repo: str,
        path: str,
        ref: str | None = None,
    ) -> bytes | None: ...

    async def commit_files(
        self,
        *,
        owner: str,
        repo: str,
        branch: str,
        upserts: dict[str, bytes],
        deletes: set[str],
        message: str,
        expected_head: str | None,
    ) -> str: ...


ProgressCallback = Callable[[str, int, int], Awaitable[None]]


class GithubPublisher:
    def __init__(self, ops: GithubOps):
        self._ops = ops

    async def resolve_target(
        self,
        *,
        repo_name: str,
        private: bool,
        description: str | None,
        mode: PublishMode = PublishMode.CREATE,
    ) -> RepoCreateResult:
        """The repository to publish into, which has to exist already.

        Lemma used to create it, and that never worked under the GitHub App:
        `POST /user/repos` needs the OAuth `repo` scope and an App user token
        carries no scopes, while an installation token is refused outright --
        GitHub marks the endpoint as not available to Apps. So the call was
        made, refused, and the publish failed with whatever GitHub said.

        Asking the person to make the repository is not a workaround for that;
        it is the only thing either identity can do. It also removes a limit
        nobody chose: creation only ever targeted the authenticated user's own
        namespace, so a pod could not be published to an organisation at all.

        `private` and `description` are kept in the signature and unused: they
        describe a repository we no longer make, and the caller still records
        them on the job.
        """
        del private, description
        owner, _, bare = repo_name.rpartition("/")
        existing = await self._ops.resolve_repo(name=bare, owner=owner or None)
        if existing is None:
            # Unreachable and absent are the same 404 from GitHub when the App
            # is not installed on the owner, so the error names both.
            raise GithubRepositoryNotFoundError(repo_name, install_url=_install_url())
        if mode is PublishMode.CREATE and await self._has_been_published(existing):
            raise GithubRepositoryExistsError(repo_name)
        return existing

    async def _has_been_published(self, repo: RepoCreateResult) -> bool:
        """Whether Lemma has published into this repository before.

        What `CREATE` refuses, now that an empty repository is the normal
        starting point rather than one we made a moment ago. The manifest is
        the only honest marker: a repository can hold anything else and still
        never have been a pod.
        """
        head = await self._ops.get_head(
            owner=repo.owner, repo=repo.repo, branch=repo.default_branch
        )
        if head is None:
            # An empty repository, which is exactly what CREATE is for. Note
            # this is now a returned value rather than a swallowed exception:
            # reading a transport failure as "empty" would let CREATE publish
            # over an existing pod.
            return False
        manifest = await self._ops.get_file(
            owner=repo.owner,
            repo=repo.repo,
            path=PUBLISH_MANIFEST_PATH,
            ref=head,
        )
        return manifest is not None

    async def publish(
        self,
        *,
        publish_id: str,
        mode: PublishMode,
        repo_name: str,
        private: bool,
        description: str | None,
        files: dict[str, bytes],
        readme: str,
        on_progress: ProgressCallback | None = None,
        already_created: RepoCreateResult | None = None,
        completed_paths: set[str] | None = None,
    ) -> RepoCreateResult:
        del completed_paths  # Atomic commits checkpoint as one unit.
        repo = already_created or await self.resolve_target(
            repo_name=repo_name,
            private=private,
            description=description,
            mode=mode,
        )

        head = await self._ops.get_head(
            owner=repo.owner,
            repo=repo.repo,
            branch=repo.default_branch,
        )
        old_manifest_raw = await self._ops.get_file(
            owner=repo.owner,
            repo=repo.repo,
            path=PUBLISH_MANIFEST_PATH,
            ref=head,
        )
        old_manifest = (
            parse_publish_manifest(old_manifest_raw)
            if old_manifest_raw is not None
            else None
        )
        # Response-lost retry: the repository itself is the durable checkpoint.
        if manifest_publish_id(old_manifest) == publish_id:
            await self._emit_completed_progress(
                ["README.md", *files],
                on_progress=on_progress,
            )
            return repo

        logical_files = {"README.md": readme.encode("utf-8"), **files}
        layout_options: dict[str, int] = {}
        if mode is PublishMode.UPDATE and old_manifest is None:
            # A legacy repository may already contain a large logical path.
            # Its first manifest-backed update must overwrite that path without
            # deleting anything it cannot prove Lemma owned. Keep logical files
            # unchunked for this one transition; later manifests make stale
            # managed-path cleanup safe.
            layout_options["chunk_threshold_bytes"] = max(
                (len(content) for content in logical_files.values()),
                default=0,
            )
        physical_files, new_manifest = build_publish_layout(
            logical_files,
            publish_id=publish_id,
            **layout_options,
        )
        old_managed = manifest_managed_paths(old_manifest) if old_manifest else set()
        new_managed = manifest_managed_paths(new_manifest)
        stale_paths = old_managed - new_managed

        await self._ops.commit_files(
            owner=repo.owner,
            repo=repo.repo,
            branch=repo.default_branch,
            upserts=physical_files,
            deletes=stale_paths,
            message=f"Publish Lemma pod ({publish_id})",
            expected_head=head,
        )
        await self._emit_completed_progress(
            list(logical_files),
            on_progress=on_progress,
        )
        return repo

    @staticmethod
    async def _emit_completed_progress(
        paths: list[str],
        *,
        on_progress: ProgressCallback | None,
    ) -> None:
        if on_progress is None:
            return
        total = len(paths)
        for done, path in enumerate(paths, start=1):
            await on_progress(path, done, total)
