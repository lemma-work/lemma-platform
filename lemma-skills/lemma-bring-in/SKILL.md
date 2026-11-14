---
name: lemma-bring-in
description: "Bring a team's stuff into this Lemma from where it lives today: Notion pages into Pages, Google Drive into Files (Google Docs into Pages), Airtable and Google Sheets into Tables, a file, export or link by what it holds, and Slack by answering in its channels instead of copying history. Use when someone asks to bring something in ('Bring in Notion, Google Drive and Airtable.'), or names where their work lives and wants it here. Read-only at the source. Do not use to set a Lemma up to own a result and count its past work (lemma-setup), or to design new tables, apps or automations (lemma-builder)."
---

# Bring it in

Move a team's stuff into this Lemma by what each thing is: Notion into Pages,
Google Drive into Files (a Google Doc into Pages), Airtable and Google Sheets
into Tables, and Slack into Chats, where nothing is copied: this Lemma answers
in its channels.

Take every service named through each step together, and say what you did
after each step, in a line. Only read at the source: never change, move, send
or delete anything there, and never pass `--yes`.

## 1. Connect

Run one real read on each (`notion`, `google_drive`, `airtable`,
`google_sheets`, `slack`) in the same turn, found with `--dry-run`:

```bash
lemma connectors run notion "search pages" --dry-run      # the operation and its input, nothing run
lemma connectors run notion NOTION_SEARCH_NOTION_PAGE -d '{"page_size": 10}'
```

One not connected yet answers `CONNECTION_REQUIRED` with a `connect_url`: give
the person every such link in one reply, as it is, end your turn, and run the
same calls again when they say it is done. A service missing from
`lemma connectors list`: say so in a line, and ask for an export.

## 2. Look before you ask

List the top of each, newest first:

- Notion: `NOTION_SEARCH_NOTION_PAGE` with `{"timestamp": "last_edited_time",
  "direction": "descending"}`. Pages whose parent is the workspace are the top
  (the API names no teamspaces). It sees only pages picked when connecting.
- Google Drive: `GOOGLEDRIVE_LIST_SHARED_DRIVES`, and `GOOGLEDRIVE_FIND_FILE`
  with `{"folder_id": "root"}`.
- Airtable: `AIRTABLE_LIST_BASES`, then `AIRTABLE_GET_BASE_SCHEMA`.
- Google Sheets: `GOOGLESHEETS_SEARCH_SPREADSHEETS` with `{"order_by":
  "modifiedTime desc"}`, then `GOOGLESHEETS_GET_SHEET_NAMES`.
- Slack: "list channels", the public ones.

Then one `ask_user`: at most one question per service, only for a real choice
(which base, folder or teamspace), two to four choices, likeliest first,
`multi_select` when several can come. An obvious answer: take it and say so.

Everyone in this Lemma reads what lands in it, and when it is open to the
organization, anyone there can join. So what is private to the person at the
source stays out unless they say otherwise: Notion pages in their Private
section, Drive files shared with nobody (`"shared": false`), DMs. When the
listing shows any, add one question to the same `ask_user`, "Leave them out"
first and recommended. Notion does not mark private pages: treat top-level
pages that read as one person's (a journal, their own notes) as private.

## 3. Before the long part

- Per run, at most about 200 pages, 5,000 rows per table and 500 files, newest
  first. Count first and say it ("312 pages; the newest 200 now").
- Loop in a workspace script, raw responses in files, not your context. Keep
  progress in `bring-in.json` (what was chosen, what landed, where each listing
  stopped), written as you go and read first, so a stopped run resumes.
- Notion's and Airtable's file links expire within hours: download each file
  as you reach it and `lemma files upload` it. A refused or rate-limited call:
  wait, retry once, then skip it and count it as left out.
- Nothing lands twice. A page's source id is in `/pages/.sources.json`
  (`{"notion:<id>": "/pages/Launch plan.md"}`; front matter would show above
  the page, a dot-file stays hidden), a row's in its table's unique
  `source_id`, a file's is its path. Skip what is there.

## 4. Pages: Notion

A page is `/pages/<title>.md` opening with `# <title>`; the name drops
`\ / : * ? " < > | #`, stays under 80 characters, and gets ` 2` when taken.
What it holds sits beside it, linked on a line of its own, spaces as `%20`:
sub-pages in a folder of its name (`[Budget](/pages/Launch%20plan/Budget.md)`),
images and attachments in `/pages/Launch plan-files/`, spaces in their names
as `-` (`![](/pages/Launch%20plan-files/chart.png)`, or a link for a file).

Read Notion with `NOTION_GET_PAGE_MARKDOWN` (`{"page_id": "<id>"}`), parents
before children. Its markdown carries Notion's own tags, and a page with any
HTML tag left in it opens read-only, so turn each into plain markdown: a
sub-page into its link line, an image or file into a file in `-files`, a link
to a page you brought in into its path, a callout into a `> ` quote, toggles
and columns into their text. Write pages from the script (`lemma files write
"/pages/Launch plan.md" --from page.md`), not one `pod_write_file` at a time,
and record each as it lands. A Notion database is a table: bring it in as
step 5 does, its entries' page ids as `source_id`.

## 5. Tables: Airtable and Google Sheets

Each Airtable table, and each sheet tab whose first row is headers and rows
are records, becomes a table in snake_case (the base's name first if two
collide), shared so everyone here reads every row:

```bash
lemma tables create deals --data '{"columns":[{"name":"source_id","type":"TEXT","unique":true},{"name":"name","type":"TEXT"},{"name":"stage","type":"ENUM","options":["Lead","Won","Lost"]},{"name":"amount","type":"FLOAT"},{"name":"closes_on","type":"DATE"}],"enable_rls":false}'
lemma records import deals rows.jsonl
```

- Types follow the fields (in a sheet, the values): text, email, URL, phone
  TEXT; numbers FLOAT (counts, ratings INTEGER); checkbox BOOLEAN; date DATE;
  date-time DATETIME; single select ENUM with its choices; the rest TEXT.
- A linked record: the text of its primary field (several, comma-joined).
- An attachment: a file under `/Airtable/<base>/<table>/`, its path in the
  cell (FILE_PATH, or TEXT with one path per line when there are several).
- Airtable rows: `AIRTABLE_LIST_RECORDS` (`baseId`, `tableIdOrName`, `pageSize`
  100, then `offset`); `source_id` is the record id (`rec…`).
- Sheet rows: `GOOGLESHEETS_VALUES_GET` (`range`, `start_row`, `end_row`; the
  newest are usually last); `source_id` is a column of unique values (an id,
  an email), else tab and row (`Leads!42`). A spreadsheet with other tabs too
  (a report, a model) also comes whole as a file, as step 6 says.

An import takes at most 1,000 rows, as one transaction: one `source_id` already
there refuses the whole file, so drop those first (`pod_query`: `select
source_id from deals`). The default `--intent import` keeps triggers quiet.
Check the count with `pod_query`. A table of that name with no `source_id` was
not brought in: pick another name.

## 6. Files: Google Drive

Drive keeps its folders under `/Google Drive/`, from the folder or shared drive
chosen. Walk them with `GOOGLEDRIVE_FIND_FILE` (`folder_id`, `"orderBy":
"modifiedTime desc"`, and `fields` naming `shared` and `nextPageToken`), and
let `output_path` put each download straight into place:

```bash
lemma connectors run google_drive GOOGLEDRIVE_DOWNLOAD_FILE -d '{"fileId": "<id>", "output_path": "/Google Drive/Marketing/budget.pdf"}'
```

A Google Doc becomes a page: `"mime_type": "text/markdown"`, then step 4, keyed
`google_drive:<id>`. Sheets come as `.xlsx` (from step 5, through
`GOOGLESHEETS_EXPORT_DRIVE_FILE`, into `/Google Sheets/`), Slides and Drawings
as PDF. Forms, Sites and files over 64 MB do not come: count them as left out.

## 7. Chats: Slack

Slack history is not copied. Offer in step 2's question to have this Lemma
answer in the channels that matter, busiest first. On yes, `lemma surfaces
list` says whether it is in Slack. If it is, add them (this replaces the list,
so keep the ones it has):

```bash
lemma surfaces channels slack --data '[{"channel_id": "C0123ABCD", "channel_name": "support"}]'
```

If not, it joins Slack first: load `lemma-builder`, follow its
`references/surfaces.md`, and tell the person the one step that is theirs.

## Something else

A file or export (CSV, spreadsheet, a Notion or Airtable export, a zip):
unpack it in the workspace and follow the same rules; what fits none goes to
`/Imports/<its name>/`. Another service in `lemma connectors list`: connect it
as in step 1 and sort what it has by the same rules. A link into a service:
read it through its connector. Any other link: read it and keep it as a page,
the link under its title.

## Finish

One line per service: what came in, the tab it landed in, what was left
("Notion: 200 pages in Pages; 112 older ones left."). Then one thing they could
ask about it ("Ask me which deals went quiet this month."), and when anything
was left, offer to bring in the rest. Do not list the commands you ran.
