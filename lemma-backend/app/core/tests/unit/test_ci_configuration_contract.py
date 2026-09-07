"""Contracts that keep dependency automation and CI runner usage bounded."""

from pathlib import Path


_REPO_ROOT = Path(__file__).resolve().parents[5]


def _read(path: str) -> str:
    return (_REPO_ROOT / path).read_text()


def test_dependabot_is_monthly_grouped_and_uv_native() -> None:
    """Every ecosystem is monthly, grouped, and has somewhere to put a fix.

    Asserted per ecosystem rather than by counting occurrences. The counts said
    "four of these strings appear" -- true of a config where one ecosystem had
    both and another had neither, and false of a correct config the moment a
    fifth ecosystem was added. Adding cargo coverage is what surfaced that.
    """
    import yaml

    config = _read(".github/dependabot.yml")
    assert "package-ecosystem: pip" not in config, "uv is the native ecosystem"

    updates = yaml.safe_load(config)["updates"]
    ecosystems = {entry["package-ecosystem"] for entry in updates}
    # `cargo` is here because `desktop` is a cargo workspace: without an entry a
    # Rust advisory has nothing to open a pull request against.
    assert {"uv", "npm", "github-actions", "docker", "cargo"} <= ecosystems

    for entry in updates:
        name = entry["package-ecosystem"]
        assert entry["schedule"]["interval"] == "monthly", name
        applies = {group["applies-to"] for group in entry["groups"].values()}
        assert applies == {"version-updates", "security-updates"}, name
        # Two groups need two slots. At one, an open routine pull request holds
        # the only one and security updates queue behind it indefinitely.
        assert entry["open-pull-requests-limit"] >= len(entry["groups"]), name

    for directory in (
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
    """An opt-in lane stays opt-in: reachable by label, not by every push.

    This used to also cover windows-cli-smoke.yml. That workflow is gone --
    ci.yml's `windows-cli` job was a strict superset of it, so it ran a second
    Windows runner to assert things the first one already had -- and the lane
    it guarded is now an ordinary path-filtered CI job rather than an opt-in
    one. surface-live is the remaining label-gated lane here.
    """
    e2e = _read(".github/workflows/e2e.yml")
    scenarios = _read(".github/workflows/scenarios.yml")

    assert "types: [labeled, synchronize" not in e2e
    assert "github.event.label.name == 'surface-live'" in e2e
    # The scenario lanes that boot a full stack are the other opt-in shape:
    # nightly, dispatch, or the run-scenarios label -- never every PR push.
    assert "run-scenarios" in scenarios


def test_backend_e2e_triggers_directly_without_a_label() -> None:
    """backend-e2e is the one opt-in-turned-mandatory exception here.

    It used to be gated behind a `run-e2e` label -- exactly the shape the
    sibling test above still requires of surface-live-smoke and windows-cli.
    It deliberately dropped that gate to run directly on every PR push
    instead, in parallel with "CI" rather than waiting on it (fast enough now
    at ~5-6 min, and a future required-check gate can't tolerate a workflow_run
    cascade turning a failed upstream run into a *skipped*, not failed, check).
    Pinning the absence of a label check here means a future edit that
    reintroduces one gets caught, the same way the sibling test catches it for
    the workflows still meant to have one.
    """
    e2e = _read(".github/workflows/e2e.yml")
    job = e2e.split("\n  backend-e2e:\n", 1)[1].split("\n  e2e-passed:", 1)[0]
    # Just the gate, not the whole job body -- the checkout step's ref:
    # fallback and its comment mention workflow_run harmlessly (it's simply
    # empty on any other trigger), which isn't the invariant this checks.
    condition = job.split("if: >-", 1)[1].split("runs-on:", 1)[0]

    # Logic, not prose: this job's own comments are free to say "label" (and
    # do elsewhere in the job body), so check the expression shapes a
    # label-gate or a workflow_run dependency would actually use, not the
    # bare word.
    assert "event.label" not in condition
    assert "== 'labeled'" not in condition
    assert "workflow_run" not in condition
    assert (
        "opened" in condition and "synchronize" in condition and "reopened" in condition
    )


def test_expensive_security_jobs_are_change_scoped() -> None:
    workflow = _read(".github/workflows/security.yml")

    assert "name: Detect security-relevant changes" in workflow
    assert "if: needs.changes.outputs.python == 'true'" in workflow
    assert "if: needs.changes.outputs.javascript == 'true'" in workflow
    assert "if: needs.changes.outputs.python_dependencies == 'true'" in workflow
    assert "if: needs.changes.outputs.backend_image == 'true'" in workflow
