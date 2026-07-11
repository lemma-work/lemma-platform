"""Contracts that keep dependency automation and CI runner usage bounded."""

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[5]


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text()


def test_dependabot_is_monthly_grouped_and_uv_native() -> None:
    config = _read(".github/dependabot.yml")

    assert "package-ecosystem: uv" in config
    assert "package-ecosystem: pip" not in config
    assert config.count("interval: monthly") == 4
    assert "interval: weekly" not in config
    assert config.count("applies-to: security-updates") == 4
    for directory in (
        "/agentbox",
        "/agentbox-client",
        "/lemma-backend/lemma-connectors",
        "/lemma-cli",
        "/lemma-pod-bundle",
        "/lemma-python",
        "/lemma-stack",
    ):
        assert f"- {directory}" in config


def test_backend_changes_do_not_trigger_committed_spec_codegen() -> None:
    workflow = _read(".github/workflows/ci.yml")
    codegen_filter = workflow.split("            codegen:\n", 1)[1].split(
        "\n\n  backend-unit:", 1
    )[0]

    assert "lemma-python/lemma_sdk/openapi_spec.json" in codegen_filter
    assert "lemma-backend/app/**" not in codegen_filter
    assert "scripts/**" not in codegen_filter


def test_opt_in_workflows_do_not_run_on_every_pr_sync() -> None:
    e2e = _read(".github/workflows/e2e.yml")
    windows = _read(".github/workflows/windows-daemon.yml")

    assert "types: [labeled, synchronize" not in e2e
    assert "types: [labeled, synchronize" not in windows
    assert "github.event.label.name == 'run-e2e'" in e2e
    assert "github.event.label.name == 'surface-live'" in e2e
    assert "github.event.label.name == 'windows-ci'" in windows


def test_expensive_security_jobs_are_change_scoped() -> None:
    workflow = _read(".github/workflows/security.yml")

    assert "name: Detect security-relevant changes" in workflow
    assert "if: needs.changes.outputs.python == 'true'" in workflow
    assert "if: needs.changes.outputs.javascript == 'true'" in workflow
    assert "if: needs.changes.outputs.python_dependencies == 'true'" in workflow
    assert "if: needs.changes.outputs.backend_image == 'true'" in workflow
