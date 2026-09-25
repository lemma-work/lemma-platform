# Google Tasks

Google Tasks is a lightweight to-do list manager integrated into Gmail and Calendar. Individuals and teams use it to track action items, deadlines, and simple project tasks.

**Auth config name:** `googletasks`

## Common Tasks

### List your task lists
When you need to discover the IDs of all available task lists before targeting a specific one.
```
lemma connectors operations execute googletasks GOOGLETASKS_LIST_TASK_LISTS -d '{}'
```

### Retrieve all tasks due this week
When you want to see every task across all lists that is due between March 17 and March 23, 2025.
```
lemma connectors operations execute googletasks GOOGLETASKS_LIST_ALL_TASKS -d '{"payload": {"dueMin": "2025-03-17T00:00:00Z", "dueMax": "2025-03-23T23:59:59Z"}}'
```

### Create a task in the default list
When you need to add a new task with a due date and notes to your primary list.
```
lemma connectors operations execute googletasks GOOGLETASKS_INSERT_TASK -d '{"payload": {"title": "Send email to anukul@lemma.work", "due": "2025-03-16T00:00:00Z", "notes": "Draft email about project update", "tasklist_id": "@default"}}'
```

### Update a task’s title and due date
When you need to modify an existing task’s properties—for example, change its title, move its deadline, or mark it uncompleted.
```
lemma connectors operations execute googletasks GOOGLETASKS_PATCH_TASK -d '{"payload": {"task_id": "tKx9fM3pQ7zVc1L", "tasklist_id": "@default", "title": "Send email to anukul@lemma.work (urgent)", "due": "2025-03-17T00:00:00Z", "status": "needsAction"}}'
```

### Delete a task permanently
When the user confirms they want to remove a task that is no longer needed, with no undo.
```
lemma connectors operations execute googletasks GOOGLETASKS_DELETE_TASK -d '{"payload": {"task_id": "tKx9fM3pQ7zVc1L", "tasklist_id": "@default"}}'
```

### Clear all completed tasks from a list
When the user has explicitly confirmed they want to permanently delete every completed task from the default list.
```
lemma connectors operations execute googletasks GOOGLETASKS_CLEAR_TASKS -d '{"payload": {"tasklist": "@default"}}'
```

## Tips
- `lemma connectors operations search googletasks <query>` — find more operations
- `lemma connectors operations details googletasks <OPERATION>` — see full input schema