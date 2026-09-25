# ClickUp

ClickUp unifies tasks, docs, goals, and chat in a single platform, allowing teams to plan, organize, and collaborate across projects with customizable workflows.

**Auth config name:** `clickup`

## Common Tasks

### Add a dependency to a task
Use this when a task must wait on or block another task before work can proceed.
```
lemma connectors operations execute clickup CLICKUP_ADD_DEPENDENCY -d '{"payload": {"task_id": "9hx1b", "depends_on": "9hy2c"}}'
```

### Add a tag to a task
Tag a task with an existing workspace tag like `urgent` or `bug` for fast filtering and reporting.
```
lemma connectors operations execute clickup CLICKUP_ADD_TAG_TO_TASK -d '{"payload": {"task_id": "abc123", "tag_name": "urgent"}}'
```

### Link two related tasks
Connect a source task to a target task — for example, link a design task to its engineering implementation.
```
lemma connectors operations execute clickup CLICKUP_ADD_TASK_LINK -d '{"payload": {"task_id": "9hx1b", "links_to": "9hz3d"}}'
```

### Log time on a task
Record billable or capacity hours against a specific task using a Unix timestamp range and optional tags.
```
lemma connectors operations execute clickup CLICKUP_CREATE_A_TIME_ENTRY -d '{"payload": {"start": 1715000000000, "stop": 1715003600000, "team_Id": "12345678", "tid": "9hx1b", "tags": [{"name": "client-work"}]}}'
```

### Send a chat message
Post a quick update or structured post into a team channel to keep everyone aligned.
```
lemma connectors operations execute clickup CLICKUP_CREATE_CHAT_MESSAGE -d '{"payload": {"type": "message", "content": "Designs are ready for review – feedback by Friday please.", "channel_id": "ch-245", "workspace_id": 987654}}'
```

### Update a Doc page
Revise a Doc page’s title or markdown content when requirements evolve or sprint notes need updating.
```
lemma connectors operations execute clickup CLICKUP_CLICK_UP_UPDATE_DOC_PAGE -d '{"payload": {"doc_id": "doc-001", "page_id": "pg-02", "workspace_id": "ws-789", "content": "## Q3 Goals\n- Launch beta\n- Partner connectors\n- User research sessions"}}'
```

## Tips
- `lemma connectors operations search clickup <query>` — find more operations
- `lemma connectors operations details clickup <OPERATION>` — see full input schema