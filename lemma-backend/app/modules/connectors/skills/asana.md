# Asana

Asana is a work management platform that helps teams organize, track, and manage tasks, projects, and goals. It’s used by project managers, marketing teams, engineering squads, and cross-functional groups to coordinate work at scale.

**Auth config name:** `asana`

## Common Tasks

### Create a Project
Start a new workspace container when kicking off an initiative like a quarterly campaign.
```
lemma connectors operations execute asana ASANA_CREATE_A_PROJECT -d '{"payload": {"data": {"name": "Q4 Marketing Campaign", "workspace": "1205877623831364", "team": "1205877623831368", "public": false}}}'
```

### Add Members to a Project
Give teammates access to a project so they can view and contribute to its tasks.
```
lemma connectors operations execute asana ASANA_ADD_MEMBERS_TO_PROJECT -d '{"payload": {"members": "anukul@lemma.work", "project_gid": "1205910804862507"}}'
```

### Add a Project to a Task
Associate an existing task with a project so it appears in the project’s board or list.
```
lemma connectors operations execute asana ASANA_ADD_PROJECT_FOR_TASK -d '{"payload": {"project": "1205910804862507", "task_gid": "1205910804862521"}}'
```

### Add a Task to a Section
Move a task into a specific project section (like “In Progress”) to reflect its current stage.
```
lemma connectors operations execute asana ASANA_ADD_TASK_TO_SECTION -d '{"payload": {"task_gid": "1205910804862521", "section_gid": "1205910804862515"}}'
```

### Add a Tag to a Task
Label a task with an existing tag for categorization, priority, or automation routing.
```
lemma connectors operations execute asana ASANA_ADD_TAG_TO_TASK -d '{"payload": {"tag_gid": "1205910804862501", "task_gid": "1205910804862521"}}'
```

### Add Task Dependencies
Mark other tasks as blockers so that the current task can’t be marked complete until they’re done.
```
lemma connectors operations execute asana ASANA_ADD_TASK_DEPENDENCIES -d '{"payload": {"task_gid": "1205910804862521", "dependencies": ["1205910804862522", "1205910804862523"]}}'
```

### Add Followers to a Task
Assign watchers to a task to keep them notified of updates without making them assignees.
```
lemma connectors operations execute asana ASANA_ADD_FOLLOWERS_TO_TASK -d '{"payload": {"task_gid": "1205910804862521", "followers": ["1205877623831372"]}}'
```

## Tips
- `lemma connectors operations search asana <query>` — find more operations
- `lemma connectors operations details asana <OPERATION>` — see full input schema