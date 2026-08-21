from __future__ import annotations

import json
from types import SimpleNamespace

import pytest
from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import files
from lemma_cli.cli_core import file_view as fv

runner = CliRunner()

POD = "11111111-1111-1111-1111-111111111111"

# Two-page converted markdown carrying the same page markers the backend embeds.
PAGED_MD = (
    "<!-- PAGE 1 -->\n"
    "Intro on page one.\n"
    "<!-- PAGE 2 -->\n"
    "Details on page two.\n"
    "<!-- PAGE 3 -->\n"
    "Closing on page three.\n"
)


# --------------------------------------------------------------------------- #
# Pure helpers                                                                 #
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "spec,expected",
    [
        ("3", (3, 3)),
        ("3-7", (3, 7)),
        ("3-", (3, None)),
        ("-7", (None, 7)),
        ("10:50", (10, 50)),
        ("  4 ", (4, 4)),
    ],
)
def test_parse_range_spec_valid(spec, expected):
    assert fv.parse_range_spec(spec) == expected


@pytest.mark.parametrize("spec", ["", "0", "5-2", "abc", "-", "1-x"])
def test_parse_range_spec_invalid(spec):
    with pytest.raises(ValueError):
        fv.parse_range_spec(spec)


def test_count_pages_and_slice_pages():
    assert fv.count_pages(PAGED_MD) == 3
    assert fv.count_pages("just text, no markers") == 1
    assert fv.count_pages("") == 0

    only_two = fv.slice_pages(PAGED_MD, 2, 2)
    assert "page two" in only_two
    assert "page one" not in only_two
    assert "page three" not in only_two

    two_three = fv.slice_pages(PAGED_MD, 2, 3)
    assert "page two" in two_three and "page three" in two_three
    assert "page one" not in two_three

    # No markers → full content, regardless of the requested page.
    assert fv.slice_pages("plain", 2, 4) == "plain"
    # Start past the end → empty.
    assert fv.slice_pages(PAGED_MD, 9, 9) == ""


def test_slice_lines():
    text = "a\nb\nc\nd\ne\n"
    assert fv.slice_lines(text, 2, 3) == "b\nc\n"
    assert fv.slice_lines(text, 4, None) == "d\ne\n"
    assert fv.slice_lines(text, None, 2) == "a\nb\n"
    assert fv.slice_lines(text, None, None) == text
    assert fv.slice_lines(text, 99, 100) == ""


def test_apply_caps():
    text = "1\n2\n3\n4\n5\n"
    capped, truncated = fv.apply_caps(text, max_lines=2)
    assert capped == "1\n2\n" and truncated is True

    capped, truncated = fv.apply_caps("hello world", max_chars=5)
    assert capped == "hello" and truncated is True

    capped, truncated = fv.apply_caps(text, max_chars=None, max_lines=None)
    assert capped == text and truncated is False

    # The helper is literal: 0 caps to empty. The "0 == unlimited" convention is
    # applied a layer up in the command (_char_budget converts 0 -> None).
    capped, truncated = fv.apply_caps(text, max_chars=0)
    assert capped == "" and truncated is True


@pytest.mark.parametrize(
    "mime,has_md,expected",
    [
        ("application/pdf", True, "markdown"),
        ("text/markdown", False, "text"),
        ("text/markdown", True, "text"),  # text-like source shown verbatim
        ("image/png", False, "text"),  # falls back to text; decode flags binary
        ("application/json", False, "text"),
    ],
)
def test_resolve_mode_auto(mime, has_md, expected):
    assert fv.resolve_mode("auto", mime=mime, has_markdown=has_md) == expected


def test_resolve_mode_explicit_overrides_detection():
    assert (
        fv.resolve_mode("markdown", mime="text/plain", has_markdown=False) == "markdown"
    )
    assert fv.resolve_mode("text", mime="application/pdf", has_markdown=True) == "text"


# --------------------------------------------------------------------------- #
# `file cat` command                                                          #
# --------------------------------------------------------------------------- #


class FakeFiles:
    def __init__(self, *, meta=None, raw=b"", markdown=""):
        self._meta = meta or {}
        self._raw = raw
        self._markdown = markdown
        self.calls: list[str] = []

    def get(self, path):
        return {
            "path": path,
            "mime_type": self._meta.get("mime_type"),
            "size_bytes": self._meta.get("size_bytes", len(self._raw)),
            "metadata": {"has_markdown": self._meta.get("has_markdown", False)},
        }

    def download(self, path):
        self.calls.append("download")
        return self._raw

    def download_markdown(self, path):
        self.calls.append("markdown")
        return self._markdown.encode("utf-8")


def _patch(monkeypatch, fake_files):
    class FakeClient:
        def pod(self, pod_id):
            return SimpleNamespace(files=fake_files)

    def fake_run_with_client(ctx, fn):
        return fn(FakeClient(), SimpleNamespace(config={"_runtime": {"pod": POD}}))

    monkeypatch.setattr(files, "run_with_client", fake_run_with_client)


def test_cat_text_file(monkeypatch):
    fake = FakeFiles(meta={"mime_type": "text/markdown"}, raw=b"# Notes\nhello\n")
    _patch(monkeypatch, fake)

    result = runner.invoke(app, ["--pod", POD, "file", "cat", "/me/notes.md"])

    assert result.exit_code == 0, result.stdout
    assert "hello" in result.stdout
    assert fake.calls == ["download"]  # never fetched a conversion for a text file


def test_cat_auto_uses_markdown_for_document(monkeypatch):
    fake = FakeFiles(
        meta={"mime_type": "application/pdf", "has_markdown": True}, markdown=PAGED_MD
    )
    _patch(monkeypatch, fake)

    result = runner.invoke(
        app, ["--pod", POD, "file", "cat", "/docs/report.pdf", "--pages", "2"]
    )

    assert result.exit_code == 0, result.stdout
    assert fake.calls == ["markdown"]  # never downloaded the raw PDF bytes
    assert "page two" in result.stdout
    assert "page one" not in result.stdout


def test_cat_json_payload(monkeypatch):
    fake = FakeFiles(
        meta={"mime_type": "application/pdf", "has_markdown": True}, markdown=PAGED_MD
    )
    _patch(monkeypatch, fake)

    result = runner.invoke(
        app,
        ["--json", "--pod", POD, "file", "cat", "/docs/report.pdf", "--pages", "2-3"],
    )

    assert result.exit_code == 0, result.stdout
    payload = json.loads(result.stdout)
    assert payload["mode"] == "markdown"
    assert payload["page_count"] == 3
    assert payload["page_start"] == 2 and payload["page_end"] == 3
    assert "page two" in payload["content"]


def test_cat_binary_file(monkeypatch):
    fake = FakeFiles(meta={"mime_type": "image/png"}, raw=b"\x89PNG\x00\x01\x02")
    _patch(monkeypatch, fake)

    result = runner.invoke(app, ["--pod", POD, "file", "cat", "/me/pic.png"])

    assert result.exit_code == 0, result.stdout
    assert "Binary file" in result.stdout


def test_cat_lines_and_truncation(monkeypatch):
    fake = FakeFiles(meta={"mime_type": "text/plain"}, raw=b"l1\nl2\nl3\nl4\nl5\n")
    _patch(monkeypatch, fake)

    result = runner.invoke(
        app, ["--json", "--pod", POD, "file", "cat", "/me/f.txt", "--lines", "2-3"]
    )
    payload = json.loads(result.stdout)
    assert payload["content"] == "l2\nl3\n"
    assert payload["line_start"] == 2 and payload["line_end"] == 3

    result = runner.invoke(
        app, ["--json", "--pod", POD, "file", "cat", "/me/f.txt", "--max-chars", "4"]
    )
    payload = json.loads(result.stdout)
    assert payload["truncated"] is True
    assert len(payload["content"]) == 4


def test_cat_bad_range_is_usage_error(monkeypatch):
    fake = FakeFiles(meta={"mime_type": "text/plain"}, raw=b"x")
    _patch(monkeypatch, fake)

    result = runner.invoke(
        app, ["--pod", POD, "file", "cat", "/me/f.txt", "--pages", "5-2"]
    )
    assert result.exit_code == 2


# --------------------------------------------------------------------------- #
# `file tree` rendering                                                        #
# --------------------------------------------------------------------------- #


def test_tree_renders_ascii_tree(monkeypatch):
    tree_payload = {
        "root_path": "/me",
        "files_per_directory": 5,
        "tree": {
            "path": "/me",
            "name": "me",
            "kind": "FOLDER",
            "has_more_files": False,
            "children": [
                {
                    "path": "/me/docs",
                    "name": "docs",
                    "kind": "FOLDER",
                    "has_more_files": True,
                    "children": [
                        {
                            "path": "/me/docs/a.md",
                            "name": "a.md",
                            "kind": "FILE",
                            "has_more_files": False,
                            "children": [],
                        }
                    ],
                },
                {
                    "path": "/me/notes.txt",
                    "name": "notes.txt",
                    "kind": "FILE",
                    "has_more_files": False,
                    "children": [],
                },
            ],
        },
    }

    class FakeFilesTree:
        def tree(self, path, files_per_directory=5):
            return tree_payload

    class FakeClient:
        def pod(self, pod_id):
            return SimpleNamespace(files=FakeFilesTree())

    def fake_run_with_client(ctx, fn):
        return fn(FakeClient(), SimpleNamespace(config={"_runtime": {"pod": POD}}))

    monkeypatch.setattr(files, "run_with_client", fake_run_with_client)

    result = runner.invoke(app, ["--pod", POD, "file", "tree", "/me"])

    assert result.exit_code == 0, result.stdout
    out = result.stdout
    assert "/me" in out
    assert "docs/" in out  # folders get a trailing slash
    assert "a.md" in out
    assert "notes.txt" in out
    assert "more files" in out  # has_more_files surfaced
    assert "├──" in out or "└──" in out
