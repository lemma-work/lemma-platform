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


def _nightly_prune_step() -> dict:
    """The shipped prune step, read out of the workflow rather than retyped.

    A test that restates the script proves only that two copies agree. These
    run the text that will actually execute in CI.
    """
    import yaml

    workflow = yaml.safe_load(_read(".github/workflows/release-local-images.yml"))
    steps = workflow["jobs"]["share-desktop-dmg"]["steps"]
    return next(s for s in steps if s["name"] == "Prune superseded nightly prereleases")


def _run_prune(tmp_path, releases: list[str], *, keep: str = "3") -> tuple[int, str]:
    """Run the step with a stubbed `gh`, so no delete can escape the test."""
    import os
    import subprocess

    listing = "".join(f'printf "%s\\n" "{line}"\n' for line in releases)
    stub = tmp_path / "gh"
    stub.write_text(
        "#!/bin/bash\n"
        'if [[ "$1" == "api" ]]; then\n'
        f"{listing}"
        "  exit 0\n"
        "fi\n"
        'if [[ "$1" == "release" && "$2" == "delete" ]]; then\n'
        '  echo "DELETED $3"\n'
        "  exit 0\n"
        "fi\n"
        "exit 1\n"
    )
    stub.chmod(0o755)
    script = tmp_path / "prune.sh"
    script.write_text(_nightly_prune_step()["run"])
    completed = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "lemma-work/lemma-platform",
            "GH_TOKEN": "stub",
            "KEEP": keep,
        },
    )
    return completed.returncode, completed.stdout + completed.stderr


def test_nightly_prune_keeps_a_rolling_window_of_the_newest_builds(tmp_path) -> None:
    """Twelve nightly prereleases had buried v0.7.0 fifth from the top.

    Ordering is by creation time and the newest are kept, so the release this
    run just published is always inside the window and stays installable.
    """
    returncode, output = _run_prune(
        tmp_path,
        [
            "2026-08-14T15:07:47Z\tdesktop-nightly-oldest00",
            "2026-08-18T05:36:33Z\tdesktop-nightly-newest00",
            "2026-08-17T09:39:47Z\tdesktop-nightly-fourth00",
            "2026-08-18T05:34:01Z\tdesktop-nightly-second00",
            "2026-08-17T19:13:00Z\tdesktop-nightly-third000",
        ],
    )

    assert returncode == 0, output
    deleted = {
        line.split()[1] for line in output.splitlines() if line.startswith("DELETED ")
    }
    assert deleted == {"desktop-nightly-fourth00", "desktop-nightly-oldest00"}


def test_nightly_prune_refuses_to_delete_anything_that_is_not_a_nightly(
    tmp_path,
) -> None:
    """The guard, not the filter, is what stands between a bug and a lost release.

    `gh release delete --cleanup-tag` destroys the release *and* its tag, so a
    version tag reaching that loop is unrecoverable. This feeds one straight
    past the API filter and asserts the loop stops rather than trusting it.
    """
    returncode, output = _run_prune(
        tmp_path,
        [
            "2026-08-18T05:36:33Z\tdesktop-nightly-newest00",
            "2026-08-18T05:34:01Z\tdesktop-nightly-second00",
            "2026-08-17T19:13:00Z\tdesktop-nightly-third000",
            "2026-08-15T13:18:34Z\tv0.7.0",
            "2026-08-14T15:07:47Z\tdesktop-nightly-oldest00",
        ],
    )

    assert returncode != 0
    assert "Refusing to delete a non-nightly release::v0.7.0" in output
    # It must stop *before* deleting, not merely complain on the way past.
    assert "DELETED" not in output


def test_nightly_prune_survives_a_listing_failure_without_claiming_success(
    tmp_path,
) -> None:
    """Housekeeping runs after the DMG is published, so it must not fail the build.

    But an unreadable listing must not read as a tidy release page either --
    that is how a prune quietly stops running and the page fills up again.
    """
    import os
    import subprocess

    stub = tmp_path / "gh"
    stub.write_text(
        '#!/bin/bash\nif [[ "$1" == "api" ]]; then exit 1; fi\necho "DELETED $3"\n'
    )
    stub.chmod(0o755)
    script = tmp_path / "prune.sh"
    script.write_text(_nightly_prune_step()["run"])

    completed = subprocess.run(
        ["bash", str(script)],
        capture_output=True,
        text=True,
        env={
            **os.environ,
            "PATH": f"{tmp_path}:{os.environ['PATH']}",
            "GITHUB_REPOSITORY": "lemma-work/lemma-platform",
            "GH_TOKEN": "stub",
            "KEEP": "3",
        },
    )

    assert completed.returncode == 0
    assert "Could not list nightly prereleases" in completed.stdout
    assert "DELETED" not in completed.stdout


def test_nightly_prune_asks_the_api_only_for_nightly_prereleases() -> None:
    """Both halves of the filter matter, and neither is implied by the other.

    Dropping `.prerelease` would sweep in any future `desktop-nightly-` release
    that was promoted; dropping the prefix would sweep in every prerelease.
    """
    run = _nightly_prune_step()["run"]

    assert 'select(.prerelease and (.tag_name | startswith("desktop-nightly-")))' in run
    # Deleting the tag is the point -- an orphaned tag is still clutter.
    assert "--cleanup-tag" in run
