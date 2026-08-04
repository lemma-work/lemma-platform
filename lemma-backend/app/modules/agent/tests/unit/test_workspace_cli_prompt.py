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
    # Page- and line-scoped reading of pod documents, plus the derived-artifact
    # commands, must be present so "read a few pages" maps to the cheap path.
    assert "files cat" in prompt
    assert "--pages" in prompt
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
    assert "Download a pod file with `lemma files download` before parsing it." not in prompt
