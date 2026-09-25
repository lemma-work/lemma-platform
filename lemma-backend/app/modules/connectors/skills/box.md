# Box

Box is a cloud content management and collaboration platform used by teams to securely store, share, and govern files from anywhere.

**Auth config name:** `box`

## Common Tasks

### Create a sharable link for a file
Use when you need to let external partners view a file without logging in.
```
lemma connectors operations execute box BOX_ADD_SHARED_LINK_TO_FILE -d '{"payload": {"file_id": "98475632145", "shared__link__access": "open"}}'
```

### Share a folder with collaborators
Use to grant a group of people edit access to an entire folder via a link.
```
lemma connectors operations execute box BOX_ADD_SHARED_LINK_TO_FOLDER -d '{"payload": {"folder_id": "1234567890", "shared__link__access": "collaborators"}}'
```

### Ask Box AI a question about a file
Use when you need to extract insights from a document using natural language.
```
lemma connectors operations execute box BOX_ASK_QUESTION -d '{"payload": {"mode": "single_item_qa", "items": [{"id": "98475632145", "type": "file"}], "prompt": "Summarize the main arguments in this document."}}'
```

### Apply a classification label to a file
Use to mark a file with a security classification like “Confidential” for data governance.
```
lemma connectors operations execute box BOX_ADD_CLASSIFICATION_TO_FILE -d '{"payload": {"file_id": "98475632145", "Box__Security__Classification__Key": "Confidential"}}'
```

### Watermark a sensitive file
Use to deter unauthorized sharing by overlaying a visual watermark on a file.
```
lemma connectors operations execute box BOX_APPLY_WATERMARK_TO_FILE -d '{"payload": {"file_id": "98475632145", "watermark__imprint": "default"}}'
```

### Add a member to a group
Use to give a user access to all content shared with a specific team group.
```
lemma connectors operations execute box BOX_ADD_USER_TO_GROUP -d '{"payload": {"user__id": "11446498", "group__id": "45678"}}'
```

## Tips
- `lemma connectors operations search box <query>` — find more operations
- `lemma connectors operations details box <OPERATION>` — see full input schema