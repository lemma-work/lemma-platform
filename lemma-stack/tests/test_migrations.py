from __future__ import annotations

import subprocess

import pytest

from lemma_stack.output import AdminError
from lemma_stack.release.manifest import ImageRef, ReleaseManifest
from lemma_stack.stack.migrations import run_migrations


class FakeRuntime:
    def __init__(self, results: list[subprocess.CompletedProcess[str]]) -> None:
        self.results = iter(results)
        self.calls: list[tuple[str, ...]] = []

    def run(self, *args: str, **_kwargs: object) -> subprocess.CompletedProcess[str]:
        self.calls.append(args)
        return next(self.results)


def manifest() -> ReleaseManifest:
    return ReleaseManifest(
        version="test",
        min_admin_version="0",
        images={"backend": ImageRef("ghcr.io/lemma-work/backend:test")},
    )


def result(returncode: int = 0, *, stderr: str = "") -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(
        args=[],
        returncode=returncode,
        stdout="",
        stderr=stderr,
    )


def test_runs_one_migration_chain() -> None:
    """AgentBox had its own database and alembic chain; there is one now."""

    runtime = FakeRuntime([result()])

    run_migrations(runtime, manifest())

    assert len(runtime.calls) == 1
    assert runtime.calls[0][-4:] == (
        "ghcr.io/lemma-work/backend:test",
        "alembic",
        "upgrade",
        "head",
    )
    assert "DATABASE_URL=postgresql+asyncpg://postgres:postgres@db:5432/lemma" in runtime.calls[0]


def test_lemma_migration_failure_is_reported_exactly() -> None:
    runtime = FakeRuntime([result(1, stderr="lemma migration failed")])

    with pytest.raises(AdminError, match="Lemma database migration failed"):
        run_migrations(runtime, manifest())

    assert len(runtime.calls) == 1
