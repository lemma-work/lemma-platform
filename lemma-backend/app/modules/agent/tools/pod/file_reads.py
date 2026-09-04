"""How a pod file's text and a pod file search are answered.

Lifted out of ``pydantic_adapter``, which is over the architecture ratchet's
file-size limit: these two are the reading half of that surface and are what
grew it. The tool functions there stay as the thin registration points, and the
decisions -- which text a file actually has, and whether an empty search result
is an answer -- live here where they can be read on their own.
"""

from __future__ import annotations

from app.modules.agent.domain.value_objects import JsonObject, to_json_value
from app.modules.agent.tools.pod.models import PodReadFileRequest, SearchFilesRequest
from app.modules.agent.tools.pod.pod_data_access import PodServices
from app.modules.agent.tools.pod.pod_paths import to_me_path
from app.modules.datastore.contracts import DatastoreFileNotFoundError


async def read_file_text(
    services: PodServices, request: PodReadFileRequest, resolved_path: str
) -> JsonObject:
    """Return a file's text: its own if it has any, its conversion if not."""
    entity, content = await services.file.download_file_content_by_path(
        services.ctx.pod_id, resolved_path, services.ctx
    )
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        pass
    else:
        # It has text of its own, so that text is the answer. Converting it
        # would replace the real thing with a rendering of it -- HTML would come
        # back as prose with its markup discarded, a CSV as a table someone else
        # laid out.
        return {
            "success": True,
            "path": to_me_path(entity.path, services.ctx.user_id),
            "format": "text",
            "mime_type": entity.mime_type,
            "size_bytes": entity.size_bytes,
            "truncated": len(text) > request.max_chars,
            "text": text[: request.max_chars],
        }

    # No text of its own. Documents are converted at upload precisely so they
    # can still be read, and that conversion is what the caller wanted: asking
    # for a PDF's contents and being handed `binary: true` and an instruction to
    # call again is a round trip that answers itself.
    try:
        document, markdown, page_count = await services.file.get_document_markdown(
            services.ctx.pod_id,
            resolved_path,
            services.ctx,
            page_start=request.page_start,
            page_end=request.page_end,
        )
    except DatastoreFileNotFoundError as missing:
        # Nothing to read, now or ever, or not yet -- the reader's message
        # already distinguishes those, so carry it rather than flatten it into
        # "binary file".
        return {
            "success": True,
            "path": to_me_path(entity.path, services.ctx.user_id),
            "mime_type": entity.mime_type,
            "size_bytes": entity.size_bytes,
            "binary": True,
            "hint": str(missing),
        }
    return {
        "success": True,
        "path": to_me_path(document.path, services.ctx.user_id),
        "format": "markdown",
        "converted": True,
        "mime_type": entity.mime_type,
        "page_count": page_count,
        "page_start": request.page_start,
        "page_end": request.page_end,
        "truncated": len(markdown) > request.max_chars,
        "markdown": markdown[: request.max_chars],
    }


async def search_files(
    services: PodServices, request: SearchFilesRequest
) -> JsonObject:
    """Search indexed pod files, saying when an empty result is not an answer."""
    results = await services.file.search_files(
        services.ctx.pod_id,
        request.query,
        services.ctx,
        limit=request.limit,
        search_method=request.method,
        scope_path=request.scope_path,
    )
    payload: JsonObject = {"success": True, "results": to_json_value(results)}
    if results:
        return payload
    # An empty result is two different answers wearing the same clothes:
    # "nothing in this pod matches" and "this pod is not indexed yet". Only the
    # first is an answer. Reporting the second as the first is how an agent
    # states with confidence that a pod holds nothing on a topic it holds plenty
    # on -- and it cannot tell, because a pod with no chunks searches cleanly
    # and returns [].
    #
    # Said only when the list is empty: a search that found something has
    # already answered the question, and a count on every call would be a query
    # per search for a caveat nobody needs.
    awaiting, failed = await services.file.count_files_missing_from_the_index(
        services.ctx.pod_id
    )
    notes: list[str] = []
    if awaiting:
        payload["files_awaiting_processing"] = awaiting
        notes.append(
            f"{awaiting} file(s) in this pod are still being processed and are "
            "not searchable yet; retry once processing finishes."
        )
    # Counted and worded separately from the queued ones on purpose. Waiting
    # fixes a queued file and never fixes a failed one, so telling an agent to
    # "retry once processing finishes" for a pod whose every upload failed sends
    # it round a loop that cannot end. This is also the case that was silent:
    # the queued count is zero once everything has failed, so the caveat did not
    # fire at all and an empty result was indistinguishable from a healthy pod
    # holding nothing on the subject.
    if failed:
        payload["files_failed_processing"] = failed
        notes.append(
            f"{failed} file(s) in this pod could not be processed and will "
            "never match a search. Read one with pod_read_file for the reason."
        )
    if notes:
        payload["note"] = "No matches, but " + " ".join(notes)
    return payload
