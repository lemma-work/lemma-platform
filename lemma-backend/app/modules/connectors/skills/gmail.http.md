# Gmail

Send, manage, and organize email using a native Gmail connector. Users commonly manage drafts, labels, and attachments directly from the command line.

**Auth config name:** `gmail`

## Common Tasks

### List recent drafts
Review all current draft messages, optionally filtered by sender.
```
lemma connectors operations execute gmail drafts_list --json '{"payload": {"user_id": "me", "q": "from:alice@example.com"}}'
```

### Create an email draft
Compose a new message and save it as a draft before sending.
```
lemma connectors operations execute gmail drafts_create --json '{"payload": {"user_id": "me", "body": "{\"message\": {\"raw\": \"RnJvbTogam9yZGFuQGV4YW1wbGUuY29tDQpUbzogYWxleEBleGFtcGxlLmNvbQ0KU3ViamVjdDogTWVldGluZyBUb21vcnJvdw0KDQpMZXQncyBmaW5hbGl6ZSB0aGUgYWdlbmRhLg==\"}}"}}'
```

### Send an existing draft
Deliver a draft you’ve already created to its recipients.
```
lemma connectors operations execute gmail drafts_send --json '{"payload": {"user_id": "me", "id": "12345abc"}}'
```

### Delete a draft permanently
Remove a draft you no longer need; this bypasses the trash.
```
lemma connectors operations execute gmail drafts_delete --json '{"payload": {"user_id": "me", "id": "12345abc"}}'
```

### List all labels
See every label in your mailbox to stay organized.
```
lemma connectors operations execute gmail labels_list --json '{"payload": {"user_id": "me"}}'
```

### Create a new label
Add a custom label for categorizing emails or projects.
```
lemma connectors operations execute gmail labels_create --json '{"payload": {"user_id": "me", "body": "{\"name\": \"Project Alpha\", \"labelListVisibility\": \"labelShow\", \"messageListVisibility\": \"show\"}}"}}'
```

### Download an attachment
Retrieve a specific file from a message to process or store locally.
```
lemma connectors operations execute gmail messages_attachments_get --json '{"payload": {"user_id": "me", "message_id": "187c26b4f5e", "id": "ANGjdJ8e"}}'
```

## Tips
- `lemma connectors operations search gmail <query>` — find more operations
- `lemma connectors operations details gmail <OPERATION>` — see full input schema