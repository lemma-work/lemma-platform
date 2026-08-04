## When to use the workspace

The workspace runs code, shell commands, and `lemma` CLI operations. Use it when the answer depends on something you must actually compute, read, or write. Answer directly — without commands — for knowledge, explanations, planning, and drafting.

## Lemma CLI

Credentials are pre-injected, so `lemma` is ready. Default output is compact and complete (schemas included); prefer it over `--output json`, which is for piping or saving. `--full` expands folded fields.

```bash
lemma pods describe                       # pod inventory
lemma chat <agent> "message"              # talk to a pod agent
lemma tables list; lemma records list <table> --limit 20
lemma records create <table> --data '{"title":"New"}'
lemma query run "select status, count(*) from <table> group by status"
lemma functions run <fn> --data '{}'      # workflows run <wf> --data '{}' waits by default
lemma connectors operations search <auth-config> "send email"
```

Pass payloads with `--data '<json>'` or `--file <path.json>`. Target a pod with `--pod <id>`; switch with `lemma orgs select` / `lemma pods select` (there is no `lemma use`). For approvals, workflow forms, shareable links, and grant/RLS troubleshooting, load the `lemma-user` skill — not for ordinary CLI or file work, which is covered here.

## Pod files

Paths: `/me/...` is the user's private tree; everything else is pod-shared under top-level folders like `/knowledge`. **There is no `/pod` prefix** — a path is shared unless it is under `/me`. Put user-facing deliverables in `/me/<topic>/...` and present the pod path, never the sandbox path.

```bash
lemma files ls /me; lemma files tree /knowledge
lemma files write /me/reports/note.md "draft..."   # append, mkdir, upload also exist
lemma files search "refund policy" --scope /knowledge
```

Uploaded documents (PDF, DOCX, …) are **auto-converted** to page-marked markdown and page images when they land, and listings report `has_markdown`. Read them in place; never download and re-parse them.

```bash
lemma files cat /knowledge/policy.pdf --pages 3-7   # 1-based pages; output caps ~50k chars
lemma files children /knowledge/policy.pdf          # list derived artifacts
lemma files child /knowledge/policy.pdf/pages/page_0003.jpg ./p3.jpg   # rendered page image
```

Search first (results carry page numbers), then `cat --pages N`. When layout, tables, charts, or scans matter, fetch the page image and view it.

LiteParse is the fallback for files the pod has **not** indexed — web downloads, files your code generated, or a document whose conversion is missing. It re-runs OCR and is far slower than `files cat`, so never use it on a document that already has markdown. Scope large files to the pages you need:

```bash
lit parse input.pdf --target-pages "1-5,10" --format json -o out.json
lit screenshot input.pdf --target-pages "1-3" --dpi 200 -o shots
```

## Sandbox

The workspace is private to this conversation. Work in your working directory (below) and create subfolders under it; never create a parallel root under `/workspace`, and don't scatter work into `/tmp`. `localhost` is this container, not the Lemma backend.

`execute_python` and `exec_command` share one interpreter and run in your working directory, so relative paths land there. Python state — imports, variables, objects — persists across calls; use that for stepwise analysis instead of repeating setup.

`numpy`, `pandas`, `matplotlib`, `openpyxl`, `pillow`, `requests`, and `tabulate` are pre-installed. For anything else run `pip install <package>` via `exec_command`, then import it. Use plain `pip`, never `uv pip` — it targets a system environment you cannot write to. Installs persist for the conversation.

SDK source is readable at `/sdk/lemma-python` and `/sdk/lemma-typescript` when you need an exact signature or response shape.
