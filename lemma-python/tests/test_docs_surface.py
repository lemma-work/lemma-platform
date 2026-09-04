"""Docs-don't-lie guard: assert the SDK surface our READMEs/skills reference
actually exists, so an example like `pod.workflows.start(...)` can't ship again.
This is the cheap, offline half of DRIFT-5 (full doctest of snippets needs a backend).
"""

from lemma_sdk.resources import (
    PodAgents,
    PodFunctions,
    PodQueries,
    PodWorkflows,
)


def test_workflow_run_surface_matches_docs():
    # README documents create_run / run / submit_form.
    assert hasattr(PodWorkflows, "create_run")
    assert hasattr(PodWorkflows, "run")
    assert hasattr(PodWorkflows, "submit_form")
    # `start` never existed — it was the DRIFT-1 phantom in the README.
    assert not hasattr(PodWorkflows, "start")


def test_unified_run_verb_present_on_every_runnable():
    assert hasattr(PodFunctions, "run")
    assert hasattr(PodAgents, "run")
    assert hasattr(PodQueries, "run")


def test_pod_request_escape_hatch_exists():
    from lemma_sdk.pod import Pod

    assert hasattr(Pod, "request")


def test_typed_errors_are_importable_from_package_root():
    import lemma_sdk

    for name in (
        "LemmaAPIError",
        "LemmaNotFoundError",
        "LemmaConflictError",
        "LemmaRateLimitError",
        "LemmaAuthError",
        "LemmaPermissionError",
        "LemmaServerError",
        "LemmaConnectionError",
        "LemmaTimeoutError",
    ):
        assert hasattr(lemma_sdk, name), name


def _readme() -> str:
    from pathlib import Path

    return (Path(__file__).resolve().parents[1] / "README.md").read_text()


def test_org_facade_index_names_only_attributes_lemma_has():
    """The `Facades: ...` line is an index people scan rather than read, so a
    name in it is copied verbatim; `lemma.runtime` was one that never existed."""
    import re

    from lemma_sdk import Lemma

    line = next(
        text for text in _readme().split("\n\n") if text.startswith("Facades: ")
    )
    documented = {match.group(1) for match in re.finditer(r"`lemma\.(\w+)`", line)}

    assert documented, "the README's facade index moved or changed shape"
    assert sorted(name for name in documented if not hasattr(Lemma, name)) == []


def test_org_facade_index_covers_every_public_facade():
    """The other direction: an index that has quietly stopped tracking the
    client sends readers to the generated escape hatch instead."""
    import re

    from lemma_sdk import Lemma

    line = next(
        text for text in _readme().split("\n\n") if text.startswith("Facades: ")
    )
    documented = {match.group(1) for match in re.finditer(r"`lemma\.(\w+)`", line)}
    facades = {
        name
        for name, value in vars(Lemma).items()
        if not name.startswith("_") and type(value).__name__ == "cached_property"
    }

    assert sorted(facades - documented) == []
