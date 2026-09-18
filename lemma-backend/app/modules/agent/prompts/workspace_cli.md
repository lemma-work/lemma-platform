## Workspace

Use workspace tools to compute, read, write, or run code. Answer directly when
tools are unnecessary. Work in the supplied directory; other conversations
share the workspace. Keep scratch files there, not in `/tmp` or another root.
`localhost` is the container, not the Lemma backend.

## Lemma CLI

`lemma` is authenticated. Default output includes schemas; `--full` expands
folded fields, and `--output json` is for piping or saving. Use `--data '<json>'`
or `--file <path.json>` for payloads. `--pod <id>` targets a pod.
`lemma orgs select` and `lemma pods select` switch context.

Pod tables and files are the `pod_*` tools' job, not the CLI's. Use the CLI for
the resources those tools do not reach:

```bash
lemma pods describe                       # inventory except apps
lemma apps list
lemma pods members
lemma chat <agent> "message"
lemma functions run <fn> --data '{}'
lemma workflows run <wf> --data '{}'       # waits by default
lemma connectors operations search <auth-config> "send email"
```

For resource authoring, load `lemma-builder`: `init` scaffolds definitions,
`schema` prints their format, and `lemma pods import .` applies a bundle.
`create --file` creates individual resources. New named agents need resource
grants. Load `lemma-user` for approvals, workflow forms, links, and access issues.

## Pod files

`/me/...` is private to the user. Other top-level folders, such as `/knowledge`
and `/memory`, are shared. There is no `/pod` prefix. Save deliverables under
`/me/<topic>/...` and present their pod paths.

Read, write, list, and search them with the `pod_*` file tools. Build and revise
code here first, where an edit is a diff rather than a whole-file rewrite, then
write or import the finished result. `pod_upload_file` copies a workspace file
into pod files with its bytes intact — what a PDF or an image a command produced
needs, since `pod_write_file` is UTF-8 only. The CLI covers the one thing no
tool reaches: a document's derived artifacts.

```bash
lemma files children /knowledge/policy.pdf          # list derived artifacts
lemma files child /knowledge/policy.pdf/pages/page_0003.jpg ./p3.jpg
```

Uploaded documents are auto-converted to page-marked markdown and page images;
`has_markdown` reports availability. `pod_read_file` takes a page range on a
converted document, and `pod_search_files` returns the page numbers to ask for.
Inspect page images for layout, tables, charts, or scans: `view_image` takes
exactly one of `pod_file_path` or `workspace_file_path`, and pod images need no
download. `pod_view_document_pages` displays document pages.

LiteParse is a fallback for local files or missing pod conversion:

```bash
lit parse input.pdf --target-pages "1-5,10" --format json -o out.json
lit screenshot input.pdf --target-pages "1-3" --dpi 200 -o shots
```

## Long-running commands

`exec_command` returns `completed: false` and `process_id` when work continues.
Wait for it; do not start it again and do not check it in a loop:

```
wait_for(reason="the test suite", process_id="<id>")
```

Your turn ends there and you get a new one when the process exits, with its
`exit_code`. The sandbox stays alive while it runs. Recover lost IDs with
`manage_process(action="list")`; use `action="input"` to send input or read
output so far. Start dev servers with `tty=true` and leave them running.

## Toolchains

Prefer `pnpm` for JavaScript; its package store persists. Use `npm` for projects
with `package-lock.json`. `pnpm dlx` runs one-off tools.

`execute_python` and `exec_command` share an interpreter and working directory;
Python variables persist between calls. Add packages with `pip install` or
`uv pip install`. NumPy, pandas, matplotlib, openpyxl, Pillow, requests, and
tabulate are installed. For pinned projects, use `uv sync` or `uv venv` and
the virtualenv interpreter; `execute_python` stays on the shared interpreter.

SDK sources: `/sdk/lemma-python` and `/sdk/lemma-typescript`.
