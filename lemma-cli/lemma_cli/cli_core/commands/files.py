from __future__ import annotations

import sys
from pathlib import Path

import typer

from ..confirm import confirm_destructive
from ..file_view import (
    apply_caps,
    count_pages,
    parse_range_spec,
    resolve_mode,
    slice_lines,
    slice_pages,
)
from ..io import emit, render_file_content, render_file_tree, to_plain
from ..paths import api_path
from ..sdk import pod_client
from ..state import run_with_client, state_from_ctx

# Default character cap for `file cat`, matching the agent pod_read_file tool so
# the CLI and in-process reads truncate at the same size. `--full` lifts it.
_CAT_DEFAULT_MAX_CHARS = 50_000
# Rough chars-per-token used to translate --max-tokens into a character budget.
_CHARS_PER_TOKEN = 4

app = typer.Typer(
    help=(
        "Work with pod files like a normal filesystem: ls, cat, write, append, "
        "mkdir, upload, download, mv, rm, search.\n\n"
        "Paths under /me are private to you; all other paths are pod-shared and "
        "folder grants cascade to descendants. Uploaded documents (PDF, DOC/DOCX, "
        "ODT, RTF, Markdown, text, HTML, EPUB) are auto-indexed and searchable "
        "(see `file search`); data/binary files are stored but not indexed."
    )
)


def _split_remote_target(local: Path, remote: str | None) -> tuple[str, str]:
    target = remote or f"/me/{local.name}"
    clean = api_path(target)
    parent, _, name = clean.rstrip("/").rpartition("/")
    return parent or "/", name or local.name


def _parse_duration_seconds(value: str) -> int:
    """Parse a duration like ``30m``, ``3h``, ``24h``, ``90s`` or raw seconds."""
    text = value.strip().lower()
    if not text:
        raise typer.BadParameter("duration cannot be empty")
    units = {"s": 1, "m": 60, "h": 3600, "d": 86400}
    suffix = text[-1]
    try:
        if suffix in units:
            return int(float(text[:-1]) * units[suffix])
        return int(text)
    except ValueError as exc:
        raise typer.BadParameter(
            f"invalid duration {value!r}; use e.g. 30m, 3h, 24h, or seconds"
        ) from exc


def _range_option(value: str | None, flag: str) -> tuple[int | None, int | None]:
    if value is None:
        return None, None
    try:
        return parse_range_spec(value)
    except ValueError as exc:
        raise typer.BadParameter(f"{flag}: {exc}") from exc


def _char_budget(
    max_chars: int | None, max_tokens: int | None, full: bool
) -> int | None:
    """Resolve the effective character cap.

    ``--max-chars 0`` and ``--full`` mean unlimited; ``--max-tokens`` is folded
    in as an approximate character budget. The tightest explicit limit wins.
    """
    budget: int | None
    if max_chars is not None:
        budget = None if max_chars <= 0 else max_chars
    elif full:
        budget = None
    else:
        budget = _CAT_DEFAULT_MAX_CHARS
    if max_tokens is not None and max_tokens > 0:
        token_budget = max_tokens * _CHARS_PER_TOKEN
        budget = token_budget if budget is None else min(budget, token_budget)
    return budget


def _resolve_text_content(text: str | None, from_file: Path | None) -> str:
    """Resolve write/append content from an argument, a local file, or stdin."""
    if text is not None and from_file is not None:
        raise typer.BadParameter("Pass content as an argument OR --from, not both.")
    if from_file is not None:
        return from_file.read_text(encoding="utf-8")
    if text is not None:
        return text
    if sys.stdin.isatty():
        raise typer.BadParameter(
            "No content given. Pass text as an argument, --from <file>, or pipe via stdin."
        )
    return sys.stdin.read()


# --- Navigation / reading -------------------------------------------------


@app.command("ls")
def ls_files(
    ctx: typer.Context,
    path: str = typer.Argument("/me", help="Directory to list."),
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(100, "--limit"),
) -> None:
    """List files in a directory."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.list(
            api_path(path), limit=limit
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("tree")
def tree_files(
    ctx: typer.Context,
    path: str = typer.Argument("/me"),
    pod: str | None = typer.Option(None, "--pod"),
    files_per_directory: int = typer.Option(5, "--files-per-directory", min=0, max=20),
) -> None:
    """Show the file tree under a path."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.tree(
            api_path(path), files_per_directory=files_per_directory
        ),
    )
    if result is not None:
        render_file_tree(state, result)


@app.command("cat")
def cat_file(
    ctx: typer.Context,
    path: str = typer.Argument(
        ..., help="Pod file path, e.g. /me/notes.md or /docs/report.pdf."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    mode: str = typer.Option(
        "auto",
        "--mode",
        help="auto, text, or markdown. auto shows raw text for text files and converted markdown for documents.",
    ),
    markdown: bool = typer.Option(
        False,
        "--markdown",
        help="Shortcut for --mode markdown (converted document text).",
    ),
    text: bool = typer.Option(
        False, "--text", help="Shortcut for --mode text (raw file bytes as UTF-8)."
    ),
    pages: str | None = typer.Option(
        None, "--pages", help="Page range over converted markdown, e.g. 3, 3-7, 3-, -7."
    ),
    lines: str | None = typer.Option(
        None, "--lines", help="1-based line range, e.g. 10-50, 10-, -50, 42."
    ),
    max_chars: int | None = typer.Option(
        None,
        "--max-chars",
        help="Cap output characters (default 50000; 0 = unlimited).",
    ),
    max_lines: int | None = typer.Option(
        None, "--max-lines", help="Cap output to the first N lines."
    ),
    max_tokens: int | None = typer.Option(
        None, "--max-tokens", help="Approximate token cap (~4 chars/token)."
    ),
) -> None:
    """Print a pod file's content, like `cat`.

    Reads any pod file directly to stdout: raw text for text-like files and the
    converted markdown for documents (PDF, DOCX, ...). Output is capped at 50000
    characters by default; narrow it with --pages/--lines or widen it with
    --max-chars/--max-lines/--max-tokens (or --full). Pass --json for a payload.
    """
    if markdown and text:
        raise typer.BadParameter("--markdown and --text are mutually exclusive.")
    requested_mode = "markdown" if markdown else "text" if text else mode
    if requested_mode not in ("auto", "text", "markdown"):
        raise typer.BadParameter("--mode must be auto, text, or markdown.")

    page_start, page_end = _range_option(pages, "--pages")
    if page_end is not None and page_start is None:
        page_start = 1
    line_start, line_end = _range_option(lines, "--lines")

    state = state_from_ctx(ctx)
    budget = _char_budget(max_chars, max_tokens, state.full)

    def op(client, s):  # type: ignore[no-untyped-def]
        pc = pod_client(client, s, pod)
        clean = api_path(path)
        entity = to_plain(pc.files.get(clean))
        meta = (
            entity.get("metadata") if isinstance(entity.get("metadata"), dict) else {}
        )
        resolved = resolve_mode(
            requested_mode,
            mime=entity.get("mime_type"),
            has_markdown=bool(meta.get("has_markdown")),
        )

        payload: dict[str, object] = {
            "path": entity.get("path", clean),
            "mode": resolved,
            "mime_type": entity.get("mime_type"),
            "size_bytes": entity.get("size_bytes", 0),
            "is_binary": False,
        }

        if resolved == "markdown":
            try:
                full_md = pc.files.download_markdown(clean).decode(
                    "utf-8", errors="replace"
                )
            except Exception as exc:  # noqa: BLE001 — surfaced as a usage error below
                raise ValueError(
                    f"No converted markdown available for {clean!r} "
                    f"(only document formats are converted). Original error: {exc}"
                ) from exc
            payload["page_count"] = count_pages(full_md)
            payload["page_start"] = page_start
            payload["page_end"] = page_end if page_end is not None else page_start
            body = slice_pages(full_md, page_start, page_end)
        else:
            data = pc.files.download(clean)
            try:
                body = data.decode("utf-8")
            except UnicodeDecodeError:
                payload["is_binary"] = True
                return payload

        if line_start is not None or line_end is not None:
            payload["line_start"] = line_start
            payload["line_end"] = line_end
            body = slice_lines(body, line_start, line_end)

        body, truncated = apply_caps(body, max_chars=budget, max_lines=max_lines)
        payload["content"] = body
        payload["truncated"] = truncated
        payload["returned_chars"] = len(body)
        payload["returned_lines"] = len(body.splitlines())
        return payload

    result = run_with_client(ctx, op)
    if result is not None:
        render_file_content(state, result)


@app.command("stat")
def stat_file(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Show a file or folder's metadata."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.get(api_path(path)),
    )
    if result is not None:
        emit(state, result)


# --- Writing --------------------------------------------------------------


@app.command("write")
def write_file(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="Pod file path, e.g. /me/notes.md."),
    text: str | None = typer.Argument(
        None, help="Content; omit to read --from or stdin."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    from_file: Path | None = typer.Option(
        None,
        "--from",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Read content from a local file.",
    ),
    no_search: bool = typer.Option(
        False, "--no-search", help="Store without indexing for search."
    ),
) -> None:
    """Create or overwrite a text file with content (arg, --from, or stdin).

    `echo "hello" | lemma file write /me/notes.md`
    """
    content = _resolve_text_content(text, from_file)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.write_text(
            api_path(path), content, search_enabled=not no_search
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("append")
def append_file(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    text: str | None = typer.Argument(
        None, help="Content; omit to read --from or stdin."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    from_file: Path | None = typer.Option(
        None,
        "--from",
        exists=True,
        dir_okay=False,
        readable=True,
        help="Read content from a local file.",
    ),
) -> None:
    """Append text to a file (read-modify-write); creates it if absent."""
    content = _resolve_text_content(text, from_file)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.append_text(
            api_path(path), content
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("mkdir")
def mkdir(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Create a folder."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.create_folder(
            api_path(path)
        ),
    )
    if result is not None:
        emit(state, result)


# --- Transfer -------------------------------------------------------------


@app.command("upload")
def upload_file(
    ctx: typer.Context,
    local_file: Path = typer.Argument(..., exists=True, dir_okay=False, readable=True),
    remote_path: str | None = typer.Argument(
        None, help="Remote path, usually under /me."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    description: str | None = typer.Option(None, "--description"),
    no_search: bool = typer.Option(
        False, "--no-search", help="Store without indexing for search."
    ),
) -> None:
    """Upload a local file to the pod (documents are auto-indexed for search)."""
    directory, name = _split_remote_target(local_file, remote_path)
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.upload(
            local_file,
            directory_path=directory,
            name=name,
            description=description,
            search_enabled=not no_search,
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("download")
def download_file(
    ctx: typer.Context,
    remote_path: str = typer.Argument(...),
    local_file: Path | None = typer.Argument(
        None, help="Local destination; defaults to the file's name."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    markdown: bool = typer.Option(
        False,
        "--markdown",
        help="Download the converted markdown instead of the original bytes.",
    ),
) -> None:
    """Download a remote file to a local path.

    With --markdown, downloads a document's converted markdown. Extracted figures
    and rendered pages are child files — see `file children` / `file child`.
    """
    clean = api_path(remote_path)
    suffix = ".md" if markdown else ""
    target = local_file or Path(clean.rstrip("/").rpartition("/")[2] + suffix)
    state = state_from_ctx(ctx)

    def op(client, s):  # type: ignore[no-untyped-def]
        pc = pod_client(client, s, pod)
        if markdown:
            return pc.files.download_markdown_to(clean, target)
        return pc.files.download_to(clean, target)

    result = run_with_client(ctx, op)
    if result is not None:
        emit(state, {"saved_to": str(result)})


# --- Mutation -------------------------------------------------------------


@app.command("mv")
def move_file(
    ctx: typer.Context,
    source: str = typer.Argument(...),
    dest: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Move or rename a file or folder."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.move(
            api_path(source), api_path(dest)
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("rm")
def remove_file(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete a file or folder."""
    state = state_from_ctx(ctx)
    confirm_destructive(f"Delete {path}?", yes)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.delete(api_path(path)),
    )
    emit(state, result if result is not None else {"ok": True})


# --- Derived child files --------------------------------------------------


@app.command("children")
def list_children(
    ctx: typer.Context,
    path: str = typer.Argument(..., help="A document path, e.g. /docs/report.pdf."),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """List a document's derived child files (converted markdown, figures, pages)."""
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.list_children(
            api_path(path)
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("child")
def get_child(
    ctx: typer.Context,
    path: str = typer.Argument(
        ...,
        help="Child path, e.g. /docs/report.pdf/document.md or /docs/report.pdf/pages/page_0001.jpg.",
    ),
    local_file: Path | None = typer.Argument(
        None, help="Save to this path instead of printing."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    pages: str | None = typer.Option(
        None, "--pages", help="Page range over document.md, e.g. 3, 3-7, 3-, -7."
    ),
) -> None:
    """Fetch a document's child artifact: print text (markdown) or save bytes."""
    page_start, page_end = _range_option(pages, "--pages")
    if page_end is not None and page_start is None:
        page_start = 1
    state = state_from_ctx(ctx)

    def op(client, s):  # type: ignore[no-untyped-def]
        pc = pod_client(client, s, pod)
        clean = api_path(path)
        data = pc.files.download_child(clean, page_start=page_start, page_end=page_end)
        if local_file is not None:
            local_file.write_bytes(data)
            return {"saved_to": str(local_file), "bytes": len(data)}
        try:
            return {"path": clean, "content": data.decode("utf-8")}
        except UnicodeDecodeError:
            raise typer.BadParameter(
                f"{clean!r} is binary; pass a local path to save it, e.g. "
                f"`file child {path} ./out`."
            )

    result = run_with_client(ctx, op)
    if result is not None:
        emit(state, result)


# --- Search & sharing -----------------------------------------------------


@app.command("search")
def search(
    ctx: typer.Context,
    query: str = typer.Argument(...),
    path: str | None = typer.Option(
        None, "--scope", help="Scope search to this folder."
    ),
    direct: bool = typer.Option(
        False,
        "--direct",
        help="Only the folder's immediate children (default: whole subtree).",
    ),
    method: str | None = typer.Option(
        None, "--method", help="TEXT (full-text), VECTOR (semantic), or HYBRID."
    ),
    pod: str | None = typer.Option(None, "--pod"),
    limit: int = typer.Option(10, "--limit"),
) -> None:
    """Search the pod's indexed documents (built-in RAG).

    Pod documents are auto-indexed on upload; only COMPLETED documents are
    searchable. Use --scope (+ --direct) for directory-scoped RAG and --method to
    pick full-text, semantic, or hybrid retrieval.
    """
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.search(
            query,
            limit=limit,
            scope_path=api_path(path) if path else None,
            scope_mode=("DIRECT" if direct else "SUBTREE") if path else None,
            search_method=method.upper() if method else None,
        ),
    )
    if result is not None:
        emit(state, result)


@app.command("url")
def file_url(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
) -> None:
    """Get a short-lived download URL (plus an authenticated app deep-link).

    For a public, no-login link use `file share`.
    """
    state = state_from_ctx(ctx)
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.get_url(api_path(path)),
    )
    if result is not None:
        emit(state, result)


@app.command("share")
def share_file(
    ctx: typer.Context,
    path: str = typer.Argument(...),
    pod: str | None = typer.Option(None, "--pod"),
    ttl: str | None = typer.Option(
        None,
        "--ttl",
        "--expires",
        help="Link lifetime, e.g. 30m, 3h, 24h (default 3h, max 24h).",
    ),
    max_hits: int | None = typer.Option(
        None,
        "--max-hits",
        help="Max downloads before the link is rejected (default 50, max 100).",
    ),
) -> None:
    """Mint a public, hit-capped signed URL (no login needed to open).

    The link expires (default 3h, max 24h) and serves the file at most a set
    number of times (default 50, max 100), bounding egress if it leaks.
    """
    state = state_from_ctx(ctx)
    expires_seconds = _parse_duration_seconds(ttl) if ttl is not None else None
    result = run_with_client(
        ctx,
        lambda client, s: pod_client(client, s, pod).files.create_signed_url(
            api_path(path),
            expires_seconds=expires_seconds,
            max_hits=max_hits,
        ),
    )
    if result is not None:
        emit(state, result)
