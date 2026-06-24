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
