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
