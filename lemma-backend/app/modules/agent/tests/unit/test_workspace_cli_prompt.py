"""Unit tests for the workspace-CLI prompt fragment.

Guards the steering that keeps agents reading the pod's pre-generated document
markdown (``files cat --pages`` / ``files child``) instead of downloading and
re-OCR'ing pod files through LiteParse — the regression observed where an agent
ran ``lemma files download`` + ``lit parse`` on documents the pod had already
converted at upload.
"""

from __future__ import annotations

from app.modules.agent.domain.prompts import load_workspace_cli_prompt


def test_prompt_documents_in_place_pod_document_reading():
    """The fast path (read converted markdown in place) is documented."""
    prompt = load_workspace_cli_prompt()
    # "Read a few pages" must still map to the cheap path. Page-scoped reading
    # moved to `pod_read_file` -- the CLI's `files cat --pages` duplicated it and
    # was the recipe runs reached for instead of the tool -- so the fragment has
    # to hand that job over explicitly rather than just dropping it.
    assert "`pod_read_file` takes a page range" in prompt
    assert "files cat" not in prompt
    # The derived-artifact commands have no pod_* equivalent and stay here.
    assert "files children" in prompt
    assert "files child" in prompt
    # Shared folders are top-level; there is no `/pod` prefix (see files.md).
    assert "/pod/" not in prompt
    # The agent should be told the conversion is already done at upload.
    assert "auto-converted" in prompt
    assert "has_markdown" in prompt


def test_prompt_frames_liteparse_as_local_file_fallback():
    """LiteParse is positioned as the fallback for un-indexed local files."""
    prompt = load_workspace_cli_prompt()
    assert "lit parse" in prompt
    assert "fallback" in prompt.lower()
    # The old steering that pushed every pod file through download + parse is gone.
    assert "Download pod files into the workspace before parsing" not in prompt
    assert (
        "Download a pod file with `lemma files download` before parsing it."
        not in prompt
    )


def test_a_run_on_the_mac_is_told_about_the_mac_not_the_vm():
    """Host execution: no persistent home, no preinstalled libraries, no `lit`.

    The VM's fragment promised all three, and each sends an agent on the
    user's Mac somewhere it cannot go: installing into a home folder the
    sandbox will not let it write, importing pandas the user never installed,
    running a parser that is not there.
    """
    vm = load_workspace_cli_prompt()
    mac = load_workspace_cli_prompt(host_execution=True)

    for promise in ("whole home directory persists", "NumPy", "lit parse", "/sdk/"):
        assert promise in vm
        assert promise not in mac
    assert "on the user's own Mac" in mac
    assert "`execute_python` is not available" in mac
    # The sections the two share are one text, not two copies.
    shared = vm[vm.index("## Lemma CLI") : vm.index("## Pod files")]
    assert shared in mac
    assert [line for line in mac.splitlines() if line.startswith("## ")] == [
        line for line in vm.splitlines() if line.startswith("## ")
    ]
