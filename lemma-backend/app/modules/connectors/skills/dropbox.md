# Dropbox

Dropbox is a cloud storage service for file syncing, sharing, and collaboration — used by individuals, teams, and businesses. Its APIs enable programmatic file management, member invitations, and metadata tagging.

**Auth config name:** `dropbox`

## Common Tasks

### Upload a file
Use when you need to store a new document, image, or any file up to 150 MiB in a user’s Dropbox.
```
lemma connectors operations execute dropbox DROPBOX_ALPHA_UPLOAD_FILE -d '{"payload": {"path": "/Documents/hello.txt", "content": {"data": "SGVsbG8sIHdvcmxkIQ=="}, "mode": "add", "autorename": true, "mute": false, "client_modified": "2024-09-15T14:48:00Z"}}'
```

### Share a file with someone
Use to give a specific person viewer, editor, or commenter access to a file by ID.
```
lemma connectors operations execute dropbox DROPBOX_ADD_FILE_MEMBER -d '{"payload": {"file": "id:7bh4zBmgZoAAAAAAb", "members": [{"email": "collaborator@example.com"}], "quiet": false, "access_level": {".tag": "viewer"}, "custom_message": "Please review this file."}}'
```

### Add a member to a shared folder
Apply when an owner or editor needs to invite others to a shared folder with a role.
```
lemma connectors operations execute dropbox DROPBOX_ADD_FOLDER_MEMBER_ACTION -d '{"payload": {"shared_folder_id": "1234567890", "members": [{"email": "teammate@company.com"}], "quiet": true, "custom_message": "Welcome to the shared folder!"}}'
```

### Tag a file or folder
Use to label items with organizational tags (automatically lowercased) for quick filtering.
```
lemma connectors operations execute dropbox DROPBOX_ADD_FILE_TAGS -d '{"payload": {"path": "/Projects/design_mockup.png", "tag_text": "final"}}'
```

### Invite a new team member
Run when a team admin needs to add a user to the Dropbox team with a chosen role.
```
lemma connectors operations execute dropbox DROPBOX_ADD_TEAM_MEMBERS -d '{"payload": {"new_members": [{"email": "newuser@company.com", "role": {".tag": "member_only"}, "send_welcome_email": true}], "force_async": false}}'
```

## Tips
- `lemma connectors operations search dropbox <query>` — find more operations
- `lemma connectors operations details dropbox <OPERATION>` — see full input schema