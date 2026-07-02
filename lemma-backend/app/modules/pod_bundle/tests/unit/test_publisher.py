"""GitHub publisher (fake ops), README rendering, and AI-polish degrade."""

import pytest

from app.modules.pod_bundle.domain.errors import PodBundleDomainError
from app.modules.pod_bundle.infrastructure.ai_readme import polish_readme
from app.modules.pod_bundle.infrastructure.github_publisher import (
    ComposioGithubOps,
    GithubPublisher,
    RepoCreateResult,
)
from app.modules.pod_bundle.infrastructure.readme import install_badge, render_readme


class FakeOps:
    def __init__(self, *, create_error=False, put_error_on=None):
        self.created = None
        self.puts: list[tuple[str, int]] = []
        self._create_error = create_error
        self._put_error_on = put_error_on

    async def create_repo(self, *, name, private, description):
        if self._create_error:
            raise RuntimeError("boom")
        self.created = (name, private)
        return RepoCreateResult(owner="acme", repo=name, html_url=f"https://github.com/acme/{name}")

    async def put_file(self, *, owner, repo, path, content, message):
        if self._put_error_on and path == self._put_error_on:
            raise RuntimeError("upload failed")
        self.puts.append((path, len(content)))


# --- readme ------------------------------------------------------------------


def test_render_readme_has_badge_and_counts():
    r = render_readme(
        pod_name="CRM",
        description="Leads pod",
        resource_counts={"tables": 2, "agents": 1, "functions": 0},
        owner="acme",
        repo="crm",
    )
    assert "# CRM" in r
    assert "Leads pod" in r
    assert "img.shields.io" in r
    assert "**Tables:** 2" in r and "**Agents:** 1" in r
    assert "Functions" not in r  # zero-count types omitted


def test_install_badge_links_to_import_route():
    badge = install_badge("acme", "crm")
    assert "/import/github/acme/crm" in badge


# --- publisher ---------------------------------------------------------------


async def test_publish_creates_repo_and_uploads_readme_first():
    ops = FakeOps()
    repo = await GithubPublisher(ops).publish(
        repo_name="crm",
        private=True,
        description="d",
        files={"pod.json": b"{}", "tables/leads/leads.json": b"{}"},
        readme="# CRM\nimg.shields.io",
    )
    assert repo.html_url.endswith("/acme/crm")
    assert ops.created == ("crm", True)
    paths = [p for p, _ in ops.puts]
    assert paths[0] == "README.md"
    assert "pod.json" in paths and "tables/leads/leads.json" in paths


async def test_publish_chunks_large_files():
    ops = FakeOps()
    big = b"x" * 400_000  # > threshold -> chunked
    await GithubPublisher(ops).publish(
        repo_name="crm", private=False, description=None, files={"apps/x/dist.zip": big}, readme="img.shields.io"
    )
    chunk_paths = [p for p, _ in ops.puts if ".chunk" in p]
    assert len(chunk_paths) == 3  # 400k / 150k -> 3 parts
    assert all("of0003" in p for p in chunk_paths)


async def test_publish_create_failure_raises_domain_error():
    with pytest.raises(PodBundleDomainError):
        await GithubPublisher(FakeOps(create_error=True)).publish(
            repo_name="crm", private=False, description=None, files={}, readme="img.shields.io"
        )


async def test_publish_upload_failure_raises_domain_error():
    with pytest.raises(PodBundleDomainError):
        await GithubPublisher(FakeOps(put_error_on="pod.json")).publish(
            repo_name="crm",
            private=False,
            description=None,
            files={"pod.json": b"{}"},
            readme="img.shields.io",
        )


async def test_composio_ops_create_repo_parses_full_name():
    calls = []

    async def runner(op, payload):
        calls.append((op, payload))
        if "REPOSITORY" in op:
            return {"data": {"full_name": "acme/crm", "html_url": "https://github.com/acme/crm"}}
        return {"data": {}}

    ops = ComposioGithubOps(runner)
    repo = await ops.create_repo(name="crm", private=False, description="d")
    assert repo.owner == "acme" and repo.repo == "crm"
    await ops.put_file(owner="acme", repo="crm", path="pod.json", content=b"{}", message="m")
    # put_file base64-encodes content.
    assert calls[-1][1]["content"] == "e30="  # base64 of {}


# --- ai polish ---------------------------------------------------------------


async def test_polish_none_returns_input():
    assert await polish_readme("original img.shields.io", polish_fn=None) == "original img.shields.io"


async def test_polish_degrades_on_error():
    async def boom(_):
        raise RuntimeError("model down")

    assert await polish_readme("original img.shields.io", polish_fn=boom) == "original img.shields.io"


async def test_polish_rejects_output_dropping_badge():
    async def strip_badge(_):
        return "polished but no badge"

    # Output without the install badge is discarded (keeps the deterministic one).
    assert await polish_readme("original img.shields.io", polish_fn=strip_badge) == "original img.shields.io"


async def test_polish_accepts_good_output():
    async def good(text):
        return text + " (polished) img.shields.io"

    out = await polish_readme("original img.shields.io", polish_fn=good)
    assert "polished" in out
