from __future__ import annotations

from io import BytesIO
from types import SimpleNamespace
from uuid import uuid4

from lemma_sdk.errors import LemmaNotFoundError
from lemma_sdk.resources import files as files_mod
from lemma_sdk.resources.files import PodFiles

POD = uuid4()


def _pod_files(captured: dict) -> PodFiles:
    class FakeHttpx:
        def request(self, method, url, data=None, files=None, **kw):
            captured.update(method=method, url=url, data=data, files=files)
            return SimpleNamespace(status_code=200, json=lambda: {"ok": True})

    transport = SimpleNamespace(
        generated=SimpleNamespace(get_httpx_client=lambda: FakeHttpx()),
        error_from_response=lambda *a, **k: AssertionError("unexpected error path"),
    )
    return PodFiles(transport, pod_id=POD)


def test_patch_content_sends_new_bytes_as_multipart_file(monkeypatch):
    captured: dict = {}
    pf = _pod_files(captured)
    # The endpoint takes the content as a multipart `data` FILE (not a JSON
    # string) — this is the bug that broke write/append over the API.
    monkeypatch.setattr(
        files_mod.FileDetailResponse, "from_dict", classmethod(lambda cls, d: d)
    )

    result = pf._patch_content("/me/notes.md", "hello world")

    assert captured["method"] == "patch"
    assert captured["url"].endswith("/datastore/files/by-path")
    assert captured["data"] == {"path": "/me/notes.md"}
    filename, file_obj = captured["files"]["data"]
    assert filename == "notes.md"
    assert file_obj.read() == b"hello world"
    assert result == {"ok": True}


def test_write_text_overwrites_existing_via_patch(monkeypatch):
    pf = _pod_files({})
    calls: list = []
    monkeypatch.setattr(pf, "get", lambda path: SimpleNamespace(path=path))  # exists
    monkeypatch.setattr(
        pf,
        "_patch_content",
        lambda path, content: calls.append(("patch", path, content)),
    )
    monkeypatch.setattr(
        pf, "upload_file", lambda *a, **k: calls.append(("upload", a, k))
    )

    pf.write_text("/me/notes.md", "new content")

    assert calls == [("patch", "/me/notes.md", "new content")]


def test_write_text_creates_missing_via_upload(monkeypatch):
    pf = _pod_files({})
    calls: list = []

    def _missing(path):
        raise LemmaNotFoundError(404, "not found")

    monkeypatch.setattr(pf, "get", _missing)
    monkeypatch.setattr(pf, "_patch_content", lambda *a: calls.append(("patch", a)))
    monkeypatch.setattr(
        pf,
        "upload_file",
        lambda file, **k: calls.append(("upload", k.get("path"), file.read())),
    )

    pf.write_text("/me/new.md", "fresh")

    assert calls == [("upload", "/me/new.md", b"fresh")]


def test_upload_file_translates_full_path_to_streaming_form_fields(monkeypatch):
    captured: dict = {}
    pf = _pod_files(captured)
    monkeypatch.setattr(
        files_mod.FileDetailResponse,
        "from_dict",
        classmethod(lambda cls, data: data),
    )

    result = pf.upload_file(
        BytesIO(b"content"),
        path="/me/notes/new.md",
        filename="local-name.md",
    )

    assert captured["method"] == "post"
    assert captured["data"] == {
        "directory_path": "/me/notes",
        "name": "new.md",
        "search_enabled": "true",
    }
    assert "path" not in captured["data"]
    uploaded_name, uploaded_file = captured["files"]["data"]
    assert uploaded_name == "local-name.md"
    assert uploaded_file.read() == b"content"
    assert result == {"ok": True}


def test_append_text_reads_then_writes_concatenated(monkeypatch):
    pf = _pod_files({})
    written: list = []
    monkeypatch.setattr(pf, "download", lambda path: b"first\n")
    monkeypatch.setattr(
        pf, "write_text", lambda path, content, **k: written.append((path, content))
    )

    pf.append_text("/me/log.md", "second\n")

    assert written == [("/me/log.md", "first\nsecond\n")]


def test_attach_markdown_builds_real_multipart_files(monkeypatch, tmp_path):
    image = tmp_path / "figure.png"
    image.write_bytes(b"png-bytes")
    captured: dict = {}
    pod_files = _pod_files({})

    def _capture(operation, pod_id, *, body):
        captured.update(operation=operation, pod_id=pod_id, body=body)
        captured["markdown"] = body.data.payload.read()
        captured["image"] = body.images[0].payload.read()
        return {"ok": True}

    monkeypatch.setattr(pod_files, "_call", _capture)

    result = pod_files.attach_markdown(
        "/docs/report.pdf",
        "# Replacement",
        images=[image],
    )

    assert result == {"ok": True}
    assert captured["body"].path == "/docs/report.pdf"
    assert captured["markdown"] == b"# Replacement"
    assert captured["image"] == b"png-bytes"


def test_update_acts_on_the_path_argument_not_the_one_in_the_body():
    # The argument used to be discarded entirely, so this call updated /b.md
    # while reading as if it updated /a.md.
    sent: list = []
    transport = SimpleNamespace(
        call=lambda endpoint, *args, body=None, body_model=None, **kw: sent.append(
            body
        ),
    )
    pf = PodFiles(transport, pod_id=POD)
    request = files_mod.Update(path="/b.md", new_path="/c.md")
    request["extra"] = "kept"

    pf.update("/a.md", request)

    assert sent[0].to_dict()["path"] == "/a.md"
    assert sent[0].to_dict()["new_path"] == "/c.md"
    assert sent[0].to_dict()["extra"] == "kept"
    # The caller's own request object is left alone.
    assert request.path == "/b.md"


def test_download_to_writes_chunks_without_holding_the_file(tmp_path):
    chunks = [b"one", b"two", b"three"]
    read_timeouts: list = []

    class FakeResponse:
        def __init__(self):
            self.closed = False

        def iter_bytes(self, chunk_size):
            assert chunk_size > 0
            yield from chunks

        def close(self):
            self.closed = True

    response = FakeResponse()

    def stream(endpoint, *args, read_timeout=None, **kwargs):
        read_timeouts.append(read_timeout)
        return response

    transport = SimpleNamespace(stream=stream, timeout=30.0)
    pf = PodFiles(transport, pod_id=POD)

    target = pf.download_to("/big.pdf", tmp_path / "big.pdf")

    assert target.read_bytes() == b"".join(chunks)
    assert response.closed
    # A download's bytes arrive continuously, so an idle connection is dead --
    # unlike an event stream, which is deliberately quiet while an agent thinks.
    assert read_timeouts == [30.0]
