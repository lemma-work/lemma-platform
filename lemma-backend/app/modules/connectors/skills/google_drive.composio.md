# Google Drive

Google Drive is a cloud file-storage and collaboration platform used by teams to create, share, and manage documents, spreadsheets, media, and folders. Anyone with a Google account can store files, set granular permissions, and organize content across shared drives.

**Auth config name:** `google_drive`

## Common Tasks

### Create a new folder
When you need to set up a folder structure, provide a name and optionally a parent folder ID where the folder should live.
```
lemma connectors operations execute google_drive GOOGLEDRIVE_CREATE_FOLDER -d '{"payload": {"name": "Q4 Marketing Assets", "parent_id": "1a2B3cD4eF5gH6iJ7kL"}}'
```

### Create a text document from raw content
Use this to generate a Google Doc, plain text file, or other document directly from string content without an existing file on disk.
```
lemma connectors operations execute google_drive GOOGLEDRIVE_CREATE_FILE_FROM_TEXT -d '{"payload": {"file_name": "Project Kickoff Notes", "text_content": "Attendees: Taylor, Jordan, Casey. Agenda: timeline, budget, risks.", "mime_type": "application/vnd.google-apps.document", "parent_id": "1a2B3cD4eF5gH6iJ7kL"}}'
```

### Upload a file from the pod
Upload any file (PDF, image, .docx, a zip) into a Drive folder. `file_to_upload` takes a pod file reference, and the file keeps its name and type.
```
lemma connectors operations execute google_drive GOOGLEDRIVE_UPLOAD_FILE -d '{"folder_to_upload_to": "1a2B3cD4eF5gH6iJ7kL"}' --attach file_to_upload=/me/logo_package.zip
```
From an agent, pass `"file_to_upload": {"pod_path": "/me/logo_package.zip"}` in the arguments. A file on your own machine: `--attach file_to_upload=@./logo_package.zip`.

### Share a file with a specific person
Grant read, comment, or edit access to a file for an individual user by email. Set `role` and `type` accordingly.
```
lemma connectors operations execute google_drive GOOGLEDRIVE_CREATE_PERMISSION -d '{"payload": {"file_id": "1xYz9AbC8dEf7GhI2jKl", "role": "writer", "type": "user", "email_address": "jordan.lee@company.com", "email_message": "You now have edit access to the style guide."}}'
```

### Move a file into an additional folder
Add a file to a second folder without removing it from its original location—useful for making a document appear in multiple project collections.
```
lemma connectors operations execute google_drive GOOGLEDRIVE_ADD_PARENT -d '{"payload": {"id": "1a2B3cD4eF5gH6iJ7kL", "fileId": "1xYz9AbC8dEf7GhI2jKl", "supportsAllDrives": true}}'
```

### Duplicate a file with advanced options
Copy an existing file, assign a new name and parent folder, and optionally star it or include labels.
```
lemma connectors operations execute google_drive GOOGLEDRIVE_COPY_FILE_ADVANCED -d '{"payload": {"fileId": "1xYz9AbC8dEf7GhI2jKl", "name": "Copy of Style Guide Q3", "parents": ["1a2B3cD4eF5gH6iJ7kL"], "starred": true}}'
```

### Leave a general comment on a file
Add a file-level comment visible to everyone with access. Omit anchor fields to keep the comment on the whole document.
```
lemma connectors operations execute google_drive GOOGLEDRIVE_CREATE_COMMENT -d '{"payload": {"file_id": "1xYz9AbC8dEf7GhI2jKl", "content": "Let’s finalize the tone-of-voice section by Friday."}}'
```

## Tips
- `lemma connectors operations search google_drive <query>` — find more operations
- `lemma connectors operations details google_drive <OPERATION>` — see full input schema