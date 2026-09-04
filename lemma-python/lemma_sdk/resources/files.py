from __future__ import annotations

from contextlib import ExitStack
from copy import copy
from io import BytesIO
import mimetypes
from pathlib import Path
from typing import Any, BinaryIO, Iterator

from ..errors import LemmaNotFoundError
from ..openapi_client.api.files import (
    file_child_get,
    file_children_list,
    file_delete,
    file_download,
    file_folder_create,
    file_get,
    file_get_by_id,
    file_list,
    file_markdown_attach,
    file_markdown_detach,
    file_search,
    file_signed_url,
    file_tree,
    file_update,
    file_url,
)
from ..openapi_client.models.attach import Attach
from ..openapi_client.models.create_folder_request import CreateFolderRequest
from ..openapi_client.models.directory_tree_response import DirectoryTreeResponse
from ..openapi_client.models.file_children_response import FileChildrenResponse
from ..openapi_client.models.file_detail_response import FileDetailResponse
from ..openapi_client.models.file_list_response import FileListResponse
from ..openapi_client.models.file_search_request import FileSearchRequest
from ..openapi_client.models.file_search_response import FileSearchResponse
from ..openapi_client.models.file_signed_url_request import FileSignedUrlRequest
from ..openapi_client.models.file_signed_url_response import FileSignedUrlResponse
from ..openapi_client.models.file_url_response import FileUrlResponse
from ..openapi_client.models.update import Update
from ..openapi_client.types import File
from .base import BoundResource


class PodFiles(BoundResource):
    def list(
        self,
        path: str = "/",
        *,
        limit: int = 100,
        page_token: str | None = None,
    ) -> FileListResponse:
        """One page of a directory's entries.

        A directory with more entries than ``limit`` is truncated, and the
        response's ``next_page_token`` is the only way to see the rest --
        without passing it back, a caller that must be complete (an export, a
        sync) silently sees a prefix. See :meth:`list_all`.
        """
        kwargs: dict[str, object] = {"directory_path": path, "limit": limit}
        if page_token is not None:
            kwargs["page_token"] = page_token
        return self._call(file_list, self._pod_uuid(), **kwargs)

    def list_all(self, path: str = "/", *, page_size: int = 500) -> list[Any]:
        """Every entry in a directory, paged to exhaustion.

        The API caps a page and the directory-tree endpoint caps files per
        directory, so "list a directory" and "list all of a directory" are
        different operations. Anything that has to be complete wants this one.
        """
        entries: list[Any] = []
        token: str | None = None
        while True:
            page = self.list(path, limit=page_size, page_token=token)
            entries.extend(getattr(page, "items", None) or [])
            token = getattr(page, "next_page_token", None)
            if not isinstance(token, str) or not token:
                return entries

    def get(self, path: str) -> FileDetailResponse:
        return self._call(file_get, self._pod_uuid(), path=path)

    def get_by_id(self, file_id: str) -> FileDetailResponse:
        """Read a file by id.

        Prefer this over :meth:`get` for anything stored or shared. A path is
        not stable: ``/me/...`` is an alias resolved against whoever is asking,
        so the same path names a different file for a different caller, and any
        path breaks on rename.
        """
        return self._call(file_get_by_id, self._pod_uuid(), file_id)

    def get_url(self, path: str) -> FileUrlResponse:
        """URLs for a file.

        Returns both a short-lived download ``url`` (a real signed object-store
        URL, or a tokenized backend URL) and a permanent authenticated
        ``app_url`` deep-link that opens the file in the Lemma frontend for any
        signed-in pod member.
        """
        return self._call(file_url, self._pod_uuid(), path=path)

    def create_signed_url(
        self,
        path: str,
        *,
        expires_seconds: int | None = None,
        max_hits: int | None = None,
    ) -> FileSignedUrlResponse:
        """Mint a public, hit-capped short signed URL for a file.

        The returned ``signed_url`` needs no login to open, expires after
        ``expires_seconds`` (default 3h, max 24h), and serves the file at most
        ``max_hits`` times (default 50, max 100). Both bounds are clamped
        server-side, so you can pass user input directly. Use it to share a file
        with someone outside the pod, or to hand an agent a short link to pass
        around — the hit cap keeps a leaked link from running up egress.
        """
        body: dict[str, int] = {}
        if expires_seconds is not None:
            body["expires_seconds"] = expires_seconds
        if max_hits is not None:
            body["max_hits"] = max_hits
        return self._call(
            file_signed_url,
            self._pod_uuid(),
            body=body,
            body_model=FileSignedUrlRequest,
            path=path,
        )

    def create_folder(
        self,
        path: str,
        *,
        description: str | None = None,
        visibility: str | None = None,
    ) -> FileDetailResponse:
        body = {"path": path}
        if description is not None:
            body["description"] = description
        if visibility is not None:
            body["visibility"] = visibility
        return self._call(
            file_folder_create,
            self._pod_uuid(),
            body=body,
            body_model=CreateFolderRequest,
        )

    def update(self, path: str, request: Update) -> FileDetailResponse:
        """Update the file or folder at ``path``.

        ``path`` names what to act on and wins over ``Update.path``, which is
        the same field on the wire. The argument used to be discarded, so a
        request whose two paths disagreed acted on the one buried in the body
        while the call site read as if it acted on the argument.
        """
        body = copy(request)
        body.path = path
        return self._call(file_update, self._pod_uuid(), body=body)

    def move(self, path: str, new_path: str) -> FileDetailResponse:
        """Move or rename a file or folder."""
        return self.update(path, Update(path=path, new_path=new_path))

    def write_text(
        self,
        path: str,
        content: str,
        *,
        search_enabled: bool = True,
    ) -> FileDetailResponse:
        """Create the file (if absent) or overwrite its content with ``content``.

        Works for any text-like path; use :meth:`upload` for binary content.
        """
        try:
            self.get(path)
        except LemmaNotFoundError:
            directory, _, name = path.rstrip("/").rpartition("/")
            return self.upload_file(
                BytesIO(content.encode("utf-8")),
                path=path,
                filename=name or path.lstrip("/"),
                directory_path=directory or "/",
                search_enabled=search_enabled,
            )
        return self._patch_content(path, content)

    def _patch_content(self, path: str, content: str) -> FileDetailResponse:
        """Overwrite an existing file's content. The update endpoint takes the
        new bytes as a multipart ``data`` file (not a JSON string)."""
        name = path.rstrip("/").rpartition("/")[2] or path.lstrip("/")
        response = self.generated.get_httpx_client().request(
            method="patch",
            url=f"/pods/{self._pod_uuid()}/datastore/files/by-path",
            data={"path": path},
            files={"data": (name, BytesIO(content.encode("utf-8")))},
        )
        if response.status_code >= 400:
            raise self._transport.error_from_response(
                response.status_code, None, response.content
            )
        return FileDetailResponse.from_dict(response.json())

    def append_text(
        self,
        path: str,
        content: str,
        *,
        search_enabled: bool = True,
    ) -> FileDetailResponse:
        """Append ``content`` to a text file (read-modify-write); create it if
        absent. Not concurrency-safe — last writer wins."""
        try:
            existing = self.download(path).decode("utf-8", errors="replace")
        except LemmaNotFoundError:
            existing = ""
        return self.write_text(path, existing + content, search_enabled=search_enabled)

    def delete(self, path: str) -> None:
        self._call(file_delete, self._pod_uuid(), path=path)

    def search(
        self,
        query: str,
        *,
        scope_path: str | None = None,
        scope_mode: str | None = None,
        search_method: str | None = None,
        **filters: object,
    ) -> FileSearchResponse:
        """Search the pod's indexed documents (built-in RAG).

        Files uploaded to a pod are automatically indexed (text extracted →
        chunked → embedded); only documents that reach ``COMPLETED`` status are
        searchable. Only document formats are indexed (PDF, DOC/DOCX, ODT, RTF,
        Markdown, plain text, HTML, EPUB); data/binary formats (CSV, JSON, XLSX,
        images, …) are stored but never indexed and never returned here.

        Directory-scoped RAG:

        - ``scope_path`` — restrict the search to a folder (e.g. ``"/knowledge"``
          or ``"/me/notes"``).
        - ``scope_mode`` — ``"SUBTREE"`` (the folder and all descendants, the
          default when a scope_path is set) or ``"DIRECT"`` (immediate children
          only).
        - ``search_method`` — ``"TEXT"`` (full-text), ``"VECTOR"`` (semantic), or
          ``"HYBRID"``.

        Extra keyword ``**filters`` are folded into the request as-is for
        forward/backward compatibility.
        """
        body: dict[str, object] = {"query": query, **filters}
        if scope_path is not None:
            body["scope_path"] = scope_path
        if scope_mode is not None:
            body["scope_mode"] = scope_mode
        if search_method is not None:
            body["search_method"] = search_method
        return self._call(
            file_search,
            self._pod_uuid(),
            body=body,
            body_model=FileSearchRequest,
        )

    def tree(
        self, path: str = "/", *, files_per_directory: int = 3
    ) -> DirectoryTreeResponse:
        return self._call(
            file_tree,
            self._pod_uuid(),
            root_path=path,
            files_per_directory=files_per_directory,
        )

    def download(self, path: str) -> bytes:
        """Read the whole file into memory.

        Convenient for small files; use :meth:`download_stream` or
        :meth:`download_to` for anything large, which never hold more than one
        chunk.
        """
        result = self._call(file_download, self._pod_uuid(), path=path)
        return result.payload.getvalue()

    def download_stream(
        self, path: str, *, chunk_size: int = 1024 * 1024
    ) -> Iterator[bytes]:
        """Yield the file's bytes in chunks, without buffering the whole file.

        A pod is where document corpora live, so a download inside a function
        sandbox is exactly where holding the whole file in memory runs the
        sandbox out of it. The caller must exhaust or close the iterator.
        """
        response = self._stream(
            file_download,
            self._pod_uuid(),
            path=path,
            # Unlike an event stream, a download's bytes arrive continuously, so
            # a gap this long means the connection is gone, not that the server
            # is thinking.
            read_timeout=self._transport.timeout,
        )
        try:
            yield from response.iter_bytes(chunk_size)
        finally:
            response.close()

    def list_children(self, path: str) -> FileChildrenResponse:
        """List a document's derived child files (converted markdown, extracted
        figures, and renderable pages)."""
        return self._call(file_children_list, self._pod_uuid(), path=path)

    def download_child(
        self,
        path: str,
        *,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> bytes:
        """Fetch a single child artifact by its ``/<file-path>/<artifact>`` path,
        e.g. ``/docs/report.pdf/document.md`` or
        ``/docs/report.pdf/pages/page_0001.jpg``."""
        kwargs: dict[str, object] = {"path": path}
        if page_start is not None:
            kwargs["page_start"] = page_start
        if page_end is not None:
            kwargs["page_end"] = page_end
        result = self._call(file_child_get, self._pod_uuid(), **kwargs)
        return result.payload.getvalue()

    def download_markdown(
        self,
        path: str,
        *,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> bytes:
        """Convenience: fetch a document's converted ``document.md`` (optionally a
        page range)."""
        return self.download_child(
            f"{path}/document.md", page_start=page_start, page_end=page_end
        )

    def attach_markdown(
        self,
        path: str,
        markdown: str,
        *,
        images: list[str | Path] | None = None,
    ) -> FileDetailResponse:
        """Replace a document's agent-facing markdown (bring-your-own markdown).

        Applies to non-markdown documents (PDF, Word, HTML, …); the original file
        is unchanged. ``images`` are referenced by the markdown (``![](fig.png)``)
        and stored as sibling child artifacts.
        """
        with ExitStack() as files:
            image_files = []
            for image in images or []:
                image_path = Path(image)
                image_files.append(
                    File(
                        payload=files.enter_context(image_path.open("rb")),
                        file_name=image_path.name,
                        mime_type=mimetypes.guess_type(image_path.name)[0],
                    )
                )
            body = Attach(
                path=path,
                data=File(
                    payload=BytesIO(markdown.encode("utf-8")),
                    file_name="document.md",
                    mime_type="text/markdown",
                ),
                images=image_files,
            )
            return self._call(file_markdown_attach, self._pod_uuid(), body=body)

    def detach_markdown(self, path: str) -> FileDetailResponse:
        """Drop user-provided markdown so the document reverts to extraction."""
        return self._call(file_markdown_detach, self._pod_uuid(), path=path)

    def download_to(self, path: str, local_path: str | Path) -> Path:
        """Save the pod file to ``local_path``, a chunk at a time."""
        target = Path(local_path)
        with target.open("wb") as handle:
            for chunk in self.download_stream(path):
                handle.write(chunk)
        return target

    def download_markdown_to(
        self,
        path: str,
        local_path: str | Path,
        *,
        page_start: int | None = None,
        page_end: int | None = None,
    ) -> Path:
        target = Path(local_path)
        target.write_bytes(
            self.download_markdown(path, page_start=page_start, page_end=page_end)
        )
        return target

    def upload(
        self,
        local_path: str | Path,
        path: str | None = None,
        *,
        directory_path: str = "/",
        name: str | None = None,
        description: str | None = None,
        search_enabled: bool = True,
        visibility: str | None = None,
    ) -> FileDetailResponse:
        """Upload a local file to the pod.

        When ``search_enabled`` is True (default) the file is automatically
        indexed and becomes searchable (built-in RAG) — but only if it is a
        document format (PDF, DOC/DOCX, ODT, RTF, Markdown, plain text, HTML,
        EPUB). Data/binary formats (CSV, TSV, JSON, YAML, XLSX, images, email)
        are stored but never indexed (status ``NOT_REQUIRED``) and never appear
        in search — keep structured data in tables and prose/documents in files.

        Status flows PENDING → PROCESSING → COMPLETED (searchable) / NOT_REQUIRED
        / FAILED.

        Paths under ``/me`` are private to each user (only the owner sees their
        ``/me`` files); other paths are pod-shared and folder grants cascade to
        all descendants.
        """
        target_name = name or Path(local_path).name
        target_path = path or f"{directory_path.rstrip('/')}/{target_name}"
        with Path(local_path).open("rb") as file_obj:
            return self.upload_file(
                file_obj,
                path=target_path,
                filename=target_name,
                directory_path=directory_path,
                description=description,
                search_enabled=search_enabled,
                visibility=visibility,
            )

    def upload_file(
        self,
        file: BinaryIO,
        *,
        path: str,
        filename: str,
        directory_path: str = "/",
        description: str | None = None,
        search_enabled: bool = True,
        visibility: str | None = None,
    ) -> FileDetailResponse:
        """Upload from an open binary stream (see :meth:`upload`).

        ``search_enabled`` controls indexing/searchability and only takes effect
        for document formats; data/binary formats are stored as ``NOT_REQUIRED``
        and stay out of search. ``/me`` paths are per-user private; other paths
        are pod-shared.
        """
        normalized_path = f"/{path.strip('/')}"
        target_directory, _, target_name = normalized_path.rpartition("/")
        if not target_name:
            raise ValueError("File path must include a file name")

        data = {
            # The streaming endpoint accepts the public name/directory fields,
            # not the SDK-only full-path convenience argument. Derive both from
            # ``path`` so nested paths and /me paths cannot be misplaced when
            # callers leave ``directory_path`` at its compatibility default.
            "directory_path": target_directory or "/",
            "name": target_name,
            "description": description,
            "search_enabled": str(search_enabled).lower(),
            "visibility": visibility,
        }
        response = self.generated.get_httpx_client().request(
            method="post",
            url=f"/pods/{self._pod_uuid()}/datastore/files",
            data={key: value for key, value in data.items() if value is not None},
            files={"data": (filename, file)},
        )
        if response.status_code >= 400:
            raise self._transport.error_from_response(
                response.status_code, None, response.content
            )
        return FileDetailResponse.from_dict(response.json())
