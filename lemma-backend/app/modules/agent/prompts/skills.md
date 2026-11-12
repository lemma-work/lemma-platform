## Skills

Load a matching skill when needed. Ordinary CLI and pod file operations are
covered here. `list_skills` includes pod-authored skills -- a template's own
skill says how its work is kept -- so prefer a relevant one.

Doing the work:

- `lemma-setup`: set a Lemma up to own its result: its source, past weeks,
  feed, measure and plan.
- `lemma-bring-in`: bring a team's stuff in from Notion, Google Drive,
  Airtable, Google Sheets, Slack, or a file or export.
- `lemma-user`: day-to-day operation: what needs people, approvals, grants,
  chats and groups, RLS troubleshooting.
- `lemma-widget`: inline HTML views.
- `lemma-research`: source-backed investigations.
- `lemma-data-analysis`: quantitative analysis, charts, and writing a measure.
- `lemma-artifact-author`: documents, spreadsheets, decks, PDFs, HTML.
- `lemma-form`: forms and sign-ups for people outside; responses are table rows.
- `browser`: the workspace browser the person sees, driven with `agent-browser`;
  `agent-browser open <url>` starts it.
- `liteparse-documents`: documents outside the pod or missing conversion.

Building it -- load `lemma-builder` before designing or changing resource
definitions:

- `lemma-builder`: resource design, bundles, import/export.
- `lemma-app-design`: app UX and visual design.
- `lemma-app-qa`: browser journeys and release checks.
- `lemma-evals` and `lemma-skill-creator`: evaluations, and skills the pod
  owns; `list_skills` shows them.
