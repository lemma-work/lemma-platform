## Showing your work

Use `display_resource` to show created or changed resources, records, comparisons,
charts, and deliverables. Save long documents under `/me/<topic>/...`, display
the file, and summarize the finding in chat.

- Named resources: set `type` and `name`; omit `name` to list that resource type.
- `FILE`: takes a pod path. Upload workspace deliverables first.
- `WIDGET`: use `path` to a pod file containing its HTML. Edit that file to
  update the widget; displaying again adds another widget. Load `lemma-widget`
  before creating one; use an app for React, routing, or persistent state.

`display_resource` only displays. Use `ask_user` for questions and choices;
use `request_approval` for actions requiring permission.
