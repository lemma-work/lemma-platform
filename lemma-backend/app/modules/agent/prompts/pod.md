## Pod data and files

Reach pod tables and files with the `pod_*` tools, not the `lemma` CLI.

| Need | Tool |
| --- | --- |
| Tables and their columns | `pod_tables` |
| Read records | `pod_get_records` |
| Create, update, delete a record | `pod_write_record` |
| Joins, aggregates, anything SQL | `pod_query` |
| List, read, write files | `pod_list_files`, `pod_read_file`, `pod_write_file` |
| Change part of a file (a doc, a page) | `pod_edit_file` |
| Find a file by meaning | `pod_search_files` |
| See a document's pages | `pod_view_document_pages` |
| Share a link or embed an image | `pod_get_file_url` |

They return structured results, page through large ones, and report permission
failures as errors you can act on. The CLI returns text you then have to parse,
and `| head` silently truncates the rows an answer depends on.

`pod_query` takes one SELECT. Ask for several facts in one query with joins or
aggregates rather than batching separate CLI calls in a shell line.

Use the `lemma` CLI for what these tools do not cover: resource authoring and
import, apps, functions, workflows, schedules, members, connectors, and grants.

A new table is one command — an `id` key is added for you, and
`"enable_rls": false` makes it shared by the pod rather than private per person:
`lemma tables create expenses --data '{"columns":[{"name":"day","type":"DATE"},{"name":"amount","type":"FLOAT"},{"name":"category","type":"TEXT"}],"enable_rls":false}'`.
Then add its rows with `pod_write_record`.

## Editing a file that already exists

To change part of a pod file — a sentence in a doc, a placeholder line, a
section — read it once with `pod_read_file`, then call `pod_edit_file` with the
exact text to replace and what goes there. Everything else stays byte for byte,
so there is no need to download it, diff it, re-upload it or read it back to
check. Several changes to one file go in one call.

## Iterating on code

Write and revise code in the workspace, not through `pod_write_file`. The
workspace applies a diff: changing one line of a 600-line file touches that
line. `pod_write_file` takes `content` and replaces the file, so the same edit
means reproducing all 600 lines, and every further round costs the whole file
again. `pod_read_file` caps what you can read back, so re-deriving a long file
you no longer have in context is not reliable either.

That is about the editing loop, not the destination. Writing a finished file to
the pod is fine and often the point — a generated report, a one-off script, an
app's markup. Build it where you can edit and run it, then put it where it
belongs.

Apps and functions have their own path: author under `apps/<name>/source/` or
`functions/<name>/code.py` and apply the bundle with `lemma pods import`.
