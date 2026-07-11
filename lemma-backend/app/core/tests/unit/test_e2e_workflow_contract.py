from __future__ import annotations

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[5]


def test_e2e_workflow_has_dedicated_critical_module_shards() -> None:
    workflow = (_REPO_ROOT / ".github/workflows/e2e.yml").read_text()

    for name, path in {
        "agent": "app/modules/agent/tests/e2e",
        "surfaces": "app/modules/agent_surfaces/tests/e2e",
        "datastore": "app/modules/datastore/tests/e2e",
        "function": "app/modules/function/tests/e2e",
    }.items():
        assert f"- name: {name}" in workflow
        assert path in workflow


def test_e2e_union_gate_is_separate_from_unit_aggregate() -> None:
    workflow = (_REPO_ROOT / ".github/workflows/e2e.yml").read_text()

    assert "coverage-backend/e2e-union.json" in workflow
    assert "--min-module agent=80" in workflow
    assert "--min-module agent_surfaces=80" in workflow
    assert "--min-module datastore=80" in workflow
    assert "--min-module function=80" in workflow
    assert workflow.index("Combine E2E-only coverage") < workflow.index(
        "Download validated unit coverage"
    )


def test_ci_publishes_one_authoritative_module_wise_coverage_comment() -> None:
    e2e_workflow = (_REPO_ROOT / ".github/workflows/e2e.yml").read_text()
    ci_workflow = (_REPO_ROOT / ".github/workflows/ci.yml").read_text()

    assert "Publish one authoritative PR coverage comment" in e2e_workflow
    assert "<!-- lemma-backend-coverage:overall -->" in e2e_workflow
    assert "--unit-coverage-json" in e2e_workflow
    assert "--e2e-coverage-json" in e2e_workflow
    assert "--combined-coverage-json" in e2e_workflow
    assert "Publish authoritative E2E union PR comment" not in e2e_workflow
    assert "Update PR backend coverage comment" not in e2e_workflow
    assert "Update PR backend coverage comment" not in ci_workflow
    assert e2e_workflow.count("actions/github-script@v8") == 1


def test_required_e2e_allows_hermetic_worker_scenarios() -> None:
    makefile = (_REPO_ROOT / "lemma-backend/Makefile").read_text()

    fast_filter = next(
        line for line in makefile.splitlines() if "e2e and not slow" in line
    )
    assert "not worker" not in fast_filter
    assert "not workspace" in fast_filter
    assert "not provider" in fast_filter
    assert "not protected" in fast_filter

    conftest = (_REPO_ROOT / "lemma-backend/conftest.py").read_text()
    workspace_fixtures = conftest.split("WORKSPACE_FIXTURES =", 1)[1].split("}", 1)[0]
    assert '"backend_server"' not in workspace_fixtures
