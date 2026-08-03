# Google Drive

Google Drive stores, syncs, and shares files across teams. This connector lets you manage shared drives, file comments, and change tracking directly from the CLI—used by admins, developers, and ops engineers who want quick, scriptable access.

**Auth config name:** `google_drive`

## Common Tasks

### Check your Drive storage and user info
Use when you need to verify remaining storage or confirm the authenticated user identity.
```
lemma connectors operations execute google_drive about_get --json '{"payload": {"fields": "storageQuota,user"}}'
```

### List all shared drives
Run this to quickly find all shared drives your account can access—useful before working on a specific team drive.
```
lemma connectors operations execute google_drive drives_list --json '{"payload": {}}'
```

### Retrieve details of a shared drive
Get a drive’s name, ID, and organizer when you need to confirm permissions or use its ID in other commands.
```
lemma connectors operations execute google_drive drives_get --json '{"payload": {"drive_id": "0AJx7mYkPvQh2Uk9PVA"}}'
```

### Create a new shared drive
Spin up a dedicated drive for a project, department, or client folder structure.
```
lemma connectors operations execute google_drive drives_create --json '{"payload": {"request_id": "d4e5f6a7-b8c9-4def-9012-3456789abcde", "body": "{\"name\": \"Marketing Assets Q4\"}"}}'
```

### List comments on a file
Review all feedback on a deliverable before you approve or revise it.
```
lemma connectors operations execute google_drive comments_list --json '{"payload": {"file_id": "1xYzABCdefGHIJKlMnOpqR", "page_size": "20", "include_deleted": "false"}}'
```

### View recent Drive changes
Track edits, deletions, and new files in your Drive—helpful for audit trails or sync scripts. (First get a page token via `changes_get_start_page_token`, then use it here.)
```
lemma connectors operations execute google_drive changes_list --json '{"payload": {"page_token": "20800", "spaces": "drive", "page_size": "50"}}'
```

## Tips
- `lemma connectors operations search google_drive <query>` — find more operations
- `lemma connectors operations details google_drive <OPERATION>` — see full input schema