from __future__ import annotations

from types import SimpleNamespace

from typer.testing import CliRunner

from lemma_cli.cli_core.app import app
from lemma_cli.cli_core.commands import files

runner = CliRunner()
POD = "pod-1"


class FakeFiles:
    def __init__(self):
        self.calls: list[tuple] = []

    def write_text(self, path, content, *, search_enabled=True):
        self.calls.append(("write_text", path, content, search_enabled))
        return {"path": path, "status": "COMPLETED"}

    def append_text(self, path, content, *, search_enabled=True):
        self.calls.append(("append_text", path, content))
        return {"path": path, "status": "COMPLETED"}

    def move(self, path, new_path):
        self.calls.append(("move", path, new_path))
        return {"path": new_path}

    def list_children(self, path):
        self.calls.append(("list_children", path))
        return {
            "path": path,
            "items": [
                {
                    "name": "document.md",
                    "path": f"{path}/document.md",
                    "kind": "markdown",
                },
                {
                    "name": "pages/page_0001.jpg",
                    "path": f"{path}/pages/page_0001.jpg",
                    "kind": "page",
                },
            ],
        }

    def download_child(self, path, *, page_start=None, page_end=None):
        self.calls.append(("download_child", path, page_start, page_end))
        return b"<!-- PAGE 1 -->\n\n# Title"


def _patch(monkeypatch, fake):
    class FakeClient:
        def pod(self, pod_id):
            return SimpleNamespace(files=fake)

    monkeypatch.setattr(
        files,
        "run_with_client",
        lambda ctx, fn: fn(
            FakeClient(), SimpleNamespace(config={"_runtime": {"pod": POD}})
        ),
    )


def test_write_from_argument(monkeypatch):
    fake = FakeFiles()
    _patch(monkeypatch, fake)
    result = runner.invoke(
        app, ["--pod", POD, "file", "write", "/me/notes.md", "hello world"]
    )
    assert result.exit_code == 0, result.stdout
    assert fake.calls == [("write_text", "/me/notes.md", "hello world", True)]


def test_write_from_stdin(monkeypatch):
    fake = FakeFiles()
    _patch(monkeypatch, fake)
    result = runner.invoke(
        app, ["--pod", POD, "file", "write", "/me/notes.md"], input="piped content\n"
    )
    assert result.exit_code == 0, result.stdout
    assert fake.calls == [("write_text", "/me/notes.md", "piped content\n", True)]


def test_write_no_search_flag(monkeypatch):
    fake = FakeFiles()
    _patch(monkeypatch, fake)
    result = runner.invoke(
        app, ["--pod", POD, "file", "write", "/me/data.txt", "x", "--no-search"]
    )
    assert result.exit_code == 0, result.stdout
    assert fake.calls[0] == ("write_text", "/me/data.txt", "x", False)


def test_append(monkeypatch):
    fake = FakeFiles()
    _patch(monkeypatch, fake)
    result = runner.invoke(
        app, ["--pod", POD, "file", "append", "/me/log.md", "entry line\n"]
    )
    assert result.exit_code == 0, result.stdout
    assert fake.calls == [("append_text", "/me/log.md", "entry line\n")]


def test_mv(monkeypatch):
    fake = FakeFiles()
    _patch(monkeypatch, fake)
    result = runner.invoke(app, ["--pod", POD, "file", "mv", "/me/a.md", "/me/b.md"])
    assert result.exit_code == 0, result.stdout
    assert fake.calls == [("move", "/me/a.md", "/me/b.md")]


def test_children(monkeypatch):
    fake = FakeFiles()
    _patch(monkeypatch, fake)
    result = runner.invoke(
        app, ["--json", "--pod", POD, "file", "children", "/docs/report.pdf"]
    )
    assert result.exit_code == 0, result.stdout
    assert fake.calls == [("list_children", "/docs/report.pdf")]
    assert "document.md" in result.stdout
    assert "pages/page_0001.jpg" in result.stdout


def test_child_prints_text(monkeypatch):
    fake = FakeFiles()
    _patch(monkeypatch, fake)
    result = runner.invoke(
        app,
        [
            "--json",
            "--pod",
            POD,
            "file",
            "child",
            "/docs/report.pdf/document.md",
            "--pages",
            "1",
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert fake.calls == [("download_child", "/docs/report.pdf/document.md", 1, 1)]
    assert "PAGE 1" in result.stdout


def test_child_saves_to_local(monkeypatch, tmp_path):
    fake = FakeFiles()
    _patch(monkeypatch, fake)
    out = tmp_path / "page.jpg"
    result = runner.invoke(
        app,
        [
            "--pod",
            POD,
            "file",
            "child",
            "/docs/report.pdf/pages/page_0001.jpg",
            str(out),
        ],
    )
    assert result.exit_code == 0, result.stdout
    assert out.read_bytes() == b"<!-- PAGE 1 -->\n\n# Title"


class FakeSharedFiles:
    """A paged `list_signed_urls`, shaped like the generated client's."""

    def __init__(self, pages):
        self._pages = pages
        self.page_tokens: list[str | None] = []

    def list_signed_urls(self, *, include_dead=False, page_token=None):
        self.page_tokens.append(page_token)
        links, next_page_token = self._pages[len(self.page_tokens) - 1]
        return SimpleNamespace(links=links, next_page_token=next_page_token)


def _link(code: str) -> SimpleNamespace:
    """A link record, carrying `to_dict` the way the generated models do."""
    payload = {"code": code, "path": f"/me/{code}.txt", "filename": f"{code}.txt"}
    return SimpleNamespace(to_dict=lambda payload=payload: payload)


def test_shares_renders_a_table_rather_than_a_repr(monkeypatch):
    """`files shares` is the command for auditing what you have handed out.

    Returning a wrapper object printed `namespace(links=[SignedUrlSummary(...)])`
    — `emit` renders a list of records as a table and a dict as a detail view,
    and anything else falls through untouched and prints as a repr.
    """
    fake = FakeSharedFiles([([_link("aaa"), _link("bbb")], None)])
    _patch(monkeypatch, fake)

    result = runner.invoke(app, ["files", "shares", "--pod", POD])

    assert result.exit_code == 0, result.output
    assert "namespace(" not in result.output, result.output
    assert "SignedUrlSummary(" not in result.output, result.output
    # The codes are what you revoke by, so they have to be readable.
    assert "aaa" in result.output and "bbb" in result.output, result.output


def test_shares_follows_the_page_token_to_the_end(monkeypatch):
    """A link you cannot see is a link you cannot revoke."""
    fake = FakeSharedFiles([([_link("page1")], "token-1"), ([_link("page2")], None)])
    _patch(monkeypatch, fake)

    result = runner.invoke(app, ["files", "shares", "--pod", POD])

    assert result.exit_code == 0, result.output
    assert fake.page_tokens == [None, "token-1"], fake.page_tokens
    assert "page1" in result.output and "page2" in result.output, result.output


def test_shares_stops_when_the_page_token_is_not_a_string(monkeypatch):
    """Truthiness is not the test: a non-string token used to loop forever,
    and the CLI suite hung rather than failed."""
    fake = FakeSharedFiles([([_link("only")], object())])
    _patch(monkeypatch, fake)

    result = runner.invoke(app, ["files", "shares", "--pod", POD])

    assert result.exit_code == 0, result.output
    assert fake.page_tokens == [None], fake.page_tokens
