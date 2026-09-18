"""Unit tests for the workspace-CLI prompt fragment.

Guards the steering that keeps agents reading the pod's pre-generated document
markdown in place instead of downloading and re-OCR'ing pod files through
LiteParse — the regression observed where an agent ran ``lemma files download``
+ ``lit parse`` on documents the pod had already converted at upload.

The cheap path is now the typed tools rather than the CLI. That change is the
point: the pod tools were deferred behind ``search_tools`` while this prompt
taught the ``lemma files`` equivalent in the visible prefix, so the bypass was
cheaper than the search and the measured traffic split ~201 CLI calls to 3 tool
calls. What must stay true is that "read a few pages" maps to something that
reads the existing conversion, whatever that something is called.
"""

from __future__ import annotations

from app.modules.agent.domain.prompts import load_workspace_cli_prompt


def test_prompt_documents_in_place_pod_document_reading():
    """The fast path (read converted markdown in place) is documented."""
    prompt = load_workspace_cli_prompt()
    # Page-scoped reading of pod documents must be present so "read a few
    # pages" maps to the cheap path — and must name the typed tools, because
    # this prompt teaching the CLI instead is what caused the bypass.
    assert "pod_read_file" in prompt
    assert "page range" in prompt
    assert "pod_view_document_pages" in prompt
    assert "in place" in prompt
    # The CLI equivalents are deliberately gone: a tool and a command that do
    # the same thing, with the command shown and the tool hidden, is the shape
    # that produced the bypass.
    assert "files cat" not in prompt
    assert "files download" not in prompt
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
