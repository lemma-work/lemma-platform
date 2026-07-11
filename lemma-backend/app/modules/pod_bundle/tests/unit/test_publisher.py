"""GitHub publisher (fake ops), README rendering, and AI-polish degrade."""

import pytest

from app.modules.pod_bundle.domain.errors import PodBundleDomainError
from app.modules.pod_bundle.infrastructure.ai_readme import polish_readme
from app.modules.pod_bundle.infrastructure.github_publisher import (
    ComposioGithubOps,
    GithubPublisher,
    RepoCreateResult,
)
from app.modules.pod_bundle.infrastructure.readme import (
    install_badge,
    install_badge_url,
    install_target,
    render_readme,
)


class FakeOps:
    def __init__(
        self,
        *,
        create_error=False,
        put_error_on=None,
        ambiguous_create=False,
        ambiguous_put_on=None,
    ):
        self.created = None
        self.create_calls = 0
        self.puts: list[tuple[str, int]] = []
        self._create_error = create_error
        self._put_error_on = put_error_on
        self._ambiguous_create = ambiguous_create
        self._ambiguous_put_on = ambiguous_put_on
        self._repo = None
        self._content: dict[str, bytes] = {}

    async def resolve_repo(self, *, name):
        if self._repo is None:
            return None
        return RepoCreateResult(
            owner="acme", repo=name, html_url=f"https://github.com/acme/{name}"
        )

    async def create_repo(self, *, name, private, description):
        if self._create_error:
            raise RuntimeError("boom")
        self.create_calls += 1
        self.created = (name, private)
        self._repo = name
        result = RepoCreateResult(
            owner="acme", repo=name, html_url=f"https://github.com/acme/{name}"
        )
        if self._ambiguous_create:
            raise ConnectionError("response lost")
        return result

    async def put_file(self, *, owner, repo, path, content, message):
        if self._put_error_on and path == self._put_error_on:
            raise RuntimeError("upload failed")
        self.puts.append((path, len(content)))
        self._content[path] = content
        if self._ambiguous_put_on and path == self._ambiguous_put_on:
            raise ConnectionError("response lost")

    async def file_matches(self, *, owner, repo, path, content):
        return self._content.get(path) == content


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
    assert "Leads pod" in r  # the description becomes the tagline
    assert install_badge("acme", "crm") in r.splitlines()
    # "What's inside" is a table of the present resources.
    assert "**Tables** | 2 |" in r and "**Agents** | 1 |" in r
    assert "Functions" not in r  # zero-count types omitted


def test_render_readme_default_tagline_when_no_description():
    r = render_readme(
        pod_name="CRM", description=None, resource_counts={}, owner="acme", repo="crm"
    )
    assert "ready to install" in r  # falls back to a friendly default tagline


def test_render_readme_includes_icon_when_provided():
    r = render_readme(
        pod_name="CRM",
        description="Leads pod",
        resource_counts={},
        owner="acme",
        repo="crm",
        icon_url="https://cdn.example/icon.png",
    )
    assert '<img src="https://cdn.example/icon.png"' in r


def test_install_button_is_big_branded_badge():
    badge = install_badge("acme", "crm")
    # Links to the one-click importer for the real owner/repo...
    assert "/import/github/acme/crm" in badge
    # ...as a big for-the-badge pill carrying the Lemma mark, sized up.
    assert badge == (
        f'<a href="{install_target("acme", "crm")}">'
        f'<img src="{install_badge_url()}" height="44" '
        'alt="Install to Lemma" /></a>'
    )
    assert "for-the-badge" in badge
    assert "logo=data%3Aimage" in badge  # url-encoded data: logo (the Lemma mark)
    assert 'height="44"' in badge


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


async def test_create_repo_then_publish_reuses_repo_without_recreating():
    # The publish handler creates the repo first (to know the owner for the README),
    # then pushes with already_created — the repo must not be created twice.
    ops = FakeOps()
    publisher = GithubPublisher(ops)
    repo = await publisher.create_repo(repo_name="crm", private=False, description="d")
    assert repo.owner == "acme"
    await publisher.publish(
        repo_name="crm",
        private=False,
        description="d",
        files={"pod.json": b"{}"},
        readme="img.shields.io",
        already_created=repo,
    )
    assert ops.create_calls == 1  # created once, reused for the push


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


async def test_publish_resolves_ambiguous_create_and_file_write():
    ops = FakeOps(ambiguous_create=True, ambiguous_put_on="pod.json")

    repo = await GithubPublisher(ops).publish(
        repo_name="crm",
        private=False,
        description=None,
        files={"pod.json": b"{}"},
        readme="img.shields.io",
    )

    assert repo.repo == "crm"
    assert ops._content["pod.json"] == b"{}"


async def test_publish_skips_durable_completed_paths():
    ops = FakeOps()
    repo = RepoCreateResult(
        owner="acme", repo="crm", html_url="https://github.com/acme/crm"
    )
    await GithubPublisher(ops).publish(
        repo_name="crm",
        private=False,
        description=None,
        files={"pod.json": b"{}"},
        readme="img.shields.io",
        already_created=repo,
        completed_paths={"README.md"},
    )

    assert [path for path, _ in ops.puts] == ["pod.json"]


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


async def test_polish_strips_wrapping_code_fence():
    async def fenced(_):
        return "```markdown\n# Polished img.shields.io\n```"

    out = await polish_readme("original img.shields.io", polish_fn=fenced)
    assert out == "# Polished img.shields.io"
