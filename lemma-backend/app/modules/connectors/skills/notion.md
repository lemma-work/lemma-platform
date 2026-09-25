# Notion

Notion is a unified workspace where teams write documents, manage projects, and organize knowledge in flexible pages and databases. It is used by product, engineering, and operations teams to centralize everything from meeting notes to complex project trackers.

**Auth config name:** `notion`

## Common Tasks

### Create a new page
Use this to create a new wiki doc, meeting notes, or project brief under an existing parent page or database.
```
lemma connectors operations execute notion NOTION_CREATE_NOTION_PAGE -d '{"payload": {"parent_id": "10000000-0000-0000-0000-000000000001", "title": "Q3 Product Roadmap", "markdown": "## Goals\n- Launch mobile app v2\n- Improve onboarding conversion by 15%", "icon": "🚀"}}'
```

### Add text content to a page
Use this to append paragraphs, headings, and lists to an existing page when expanding documentation or meeting notes.
```
lemma connectors operations execute notion NOTION_APPEND_TEXT_BLOCKS -d '{"payload": {"block_id": "10000000-0000-0000-0000-000000000001", "children": [{"object":"block","type":"paragraph","paragraph":{"rich_text":[{"type":"text","text":{"content":"The engineering team will begin sprint planning on Monday, January 22, 2024."}}]}},{"object":"block","type":"heading_2","heading_2":{"rich_text":[{"type":"text","text":{"content":"Key Deliverables"}}]}}]}}'
```

### Add task blocks and callouts
Use this to insert action items, warnings, or collapsible sections into a project plan or standup notes.
```
lemma connectors operations execute notion NOTION_APPEND_TASK_BLOCKS -d '{"payload": {"block_id": "10000000-0000-0000-0000-000000000001", "children": [{"object":"block","type":"to_do","to_do":{"rich_text":[{"type":"text","text":{"content":"Review accessibility audit results"}}],"checked":false}},{"object":"block","type":"callout","callout":{"rich_text":[{"type":"text","text":{"content":"Reminder: All designs must pass WCAG 2.1 AA before handoff."}}],"icon":{"type":"emoji","emoji":"⚠️"}}}]}}'
```

### Insert a structured table
Use this to add a comparison chart, status tracker, or data grid with rows and columns to a page.
```
lemma connectors operations execute notion NOTION_APPEND_TABLE_BLOCKS -d '{"payload": {"block_id": "10000000-0000-0000-0000-000000000001", "tables": [{"table_width": 3, "has_column_header": true, "rows": [{"cells": [[{"type": "text", "text": {"content": "Feature"}}], [{"type": "text", "text": {"content": "Owner"}}], [{"type": "text", "text": {"content": "Status"}}]]}, {"cells": [[{"type": "text", "text": {"content": "Dark mode"}}], [{"type": "text", "text": {"content": "Alex Rivera"}}], [{"type": "text", "text": {"content": "In Progress"}}]]}, {"cells": [[{"type": "text", "text": {"content": "SSO login"}}], [{"type": "text", "text": {"content": "Jamie Park"}}], [{"type": "text", "text": {"content": "Done"}}]]}]}]}}'
```

### Embed a code snippet
Use this to insert technical examples, configuration files, or equations into documentation.
```
lemma connectors operations execute notion NOTION_APPEND_CODE_BLOCKS -d '{"payload": {"block_id": "10000000-0000-0000-0000-000000000001", "children": [{"object":"block","type":"code","code":{"rich_text":[{"type":"text","text":{"content":"const fetchUser = async (id) => {\n  const res = await fetch(`/api/users/${id}`);\n  return res.json();\n};"}}],"language":"javascript"}}]}}'
```

### Create a database under a page
Use this to set up a structured tracker, content calendar, or task board with defined columns inside a workspace.
```
lemma connectors operations execute notion NOTION_CREATE_DATABASE -d '{"payload": {"parent_id": "10000000-0000-0000-0000-000000000001", "title": "Marketing Campaign Tracker", "properties": [{"name":"Campaign Name","type":"title"},{"name":"Channel","type":"select","options":[{"name":"Email","color":"blue"},{"name":"Social","color":"green"}]},{"name":"Launch Date","type":"date"},{"name":"Budget","type":"number"}]}}'
```

### Archive a completed page
Use this to move a finished project, outdated document, or stale draft to the trash.
```
lemma connectors operations execute notion NOTION_ARCHIVE_NOTION_PAGE -d '{"payload": {"page_id": "30000000-0000-0000-0000-000000000003", "archive": true}}'
```

## Tips
- `lemma connectors operations search notion <query>` — find more operations
- `lemma connectors operations details notion <OPERATION>` — see full input schema