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

Paths: `/me/...` is the user's private tree; everything else is pod-shared under top-level folders like `/knowledge` and `/memory`. **There is no `/pod` prefix** — a path is shared unless it is under `/me`. Put user-facing deliverables in `/me/<topic>/...` and present the pod path, never the sandbox path.

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

Search first (results carry page numbers), then `cat --pages N`. When layout, tables, charts, or scans matter, look at the page image rather than its text.

`view_image` reads either store — set `pod_file_path` (e.g. a page image at `/knowledge/policy.pdf/pages/page_0003.jpg`) or `workspace_file_path`, exactly one, never both. Point it straight at a pod path; downloading to the sandbox first is wasted work. Use `pod_view_document_pages` to page through a document, `view_image` for one image you can already name.

LiteParse is the fallback for files the pod has **not** indexed — web downloads, files your code generated, or a document whose conversion is missing. It re-runs OCR and is far slower than `files cat`. Scope large files to the pages you need:

```bash
lit parse input.pdf --target-pages "1-5,10" --format json -o out.json
lit screenshot input.pdf --target-pages "1-3" --dpi 200 -o shots
```

## Long-running commands

Installs, builds and test suites routinely outlive a single `exec_command` call, and that is fine. When one does, you get `completed: false` and a `process_id`; the command is still running, nothing was cancelled, and no output is lost. Poll until it finishes:

```
exec_command(cmd="npm ci && npm run build", timeout_seconds=300)
manage_process(action="input", process_id="<id>")   # repeat until completed: true
```

Each poll returns only the output produced since the last one, so polling a quiet build is cheap. Read `exit_code` to know whether it actually succeeded — `completed: true` only means it stopped.

Two things to avoid: never re-run a command because it hasn't finished (you get a second build racing the first), and don't kill a slow build to "retry" it. If you lose a `process_id`, `manage_process(action="list")` recovers it — it shows what is still running here plus anything you started, not the whole workspace's history. Start long-lived servers (`npm run dev`) with `tty=true` and leave them running rather than polling them to completion.

## Sandbox

The workspace is the user's, not this conversation's: other sessions may be working in it at the same time, each in its own working directory. Work in yours (below) and create subfolders under it; never create a parallel root under `/workspace`, and don't scatter work into `/tmp`. `localhost` is this container, not the Lemma backend.

`execute_python` and `exec_command` share one interpreter and run in your working directory, so relative paths land there. Python state — imports, variables, objects — persists across calls; use that for stepwise analysis instead of repeating setup.

## Toolchains

**JavaScript and TypeScript — prefer `pnpm`.** Its store is on the workspace volume, so packages are hard-linked and survive into your next conversation: repeat installs are close to instant. `pnpm dlx` is the one-shot runner. `npm`/`npx`/`node` are there for a project with a `package-lock.json` or a tool that insists.

**Python — two cases, and they use different tools.**

*Adding a package to the interpreter `execute_python` uses*: `pip install` or `uv pip install` — either works, and the install lasts the conversation. `numpy`, `pandas`, `matplotlib`, `openpyxl`, `pillow`, `requests` and `tabulate` are already there.

*Building a Python project* — anything with a `pyproject.toml` or its own pinned dependencies: use `uv` (`uv venv` + `uv pip install`, or `uv sync` with a lockfile), and run it with that venv's interpreter. `execute_python` is bound to the shared one.

SDK source is readable at `/sdk/lemma-python` and `/sdk/lemma-typescript` when you need an exact signature or response shape.
