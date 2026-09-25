# Gmail

Send, manage, and organize email using a native Gmail connector. Users commonly manage drafts, labels, and attachments directly from the command line.

**Auth config name:** `gmail`

## Common Tasks

### Send an email with attachments
`send_message` takes plain fields and builds the message itself, so there is no MIME or base64 to write. `attachments` is a list of pod file references.
```
lemma connectors operations execute gmail send_message -d '{"to": ["anukul@lemma.work"], "subject": "Q3 report", "text": "Attached."}' --attach attachments=/me/reports/q3.pdf
```
From an agent: `"attachments": [{"pod_path": "/me/reports/q3.pdf"}]`. Add `thread_id` (and `in_reply_to`, the original's Message-ID) to reply in a thread. `create_draft` takes the same fields and saves a draft instead.

### List recent drafts
Review all current draft messages, optionally filtered by sender.
```
lemma connectors operations execute gmail drafts_list -d '{"payload": {"user_id": "me", "q": "from:anukul@lemma.work"}}'
```

### Create an email draft from raw MIME
Save a message you already have as base64url RFC 822. Prefer `create_draft`, which builds it for you.
```
lemma connectors operations execute gmail drafts_create -d '{"payload": {"user_id": "me", "body": "{\"message\": {\"raw\": \"RnJvbTogam9yZGFuQGV4YW1wbGUuY29tDQpUbzogYWxleEBleGFtcGxlLmNvbQ0KU3ViamVjdDogTWVldGluZyBUb21vcnJvdw0KDQpMZXQncyBmaW5hbGl6ZSB0aGUgYWdlbmRhLg==\"}}"}}'
```

### Send an existing draft
Deliver a draft you’ve already created to its recipients.
```
lemma connectors operations execute gmail drafts_send -d '{"payload": {"user_id": "me", "id": "12345abc"}}'
```

### Delete a draft permanently
Remove a draft you no longer need; this bypasses the trash.
```
lemma connectors operations execute gmail drafts_delete -d '{"payload": {"user_id": "me", "id": "12345abc"}}'
```

### List all labels
See every label in your mailbox to stay organized.
```
lemma connectors operations execute gmail labels_list -d '{"payload": {"user_id": "me"}}'
```

### Create a new label
Add a custom label for categorizing emails or projects.
```
lemma connectors operations execute gmail labels_create -d '{"payload": {"user_id": "me", "body": "{\"name\": \"Project Alpha\", \"labelListVisibility\": \"labelShow\", \"messageListVisibility\": \"show\"}}"}}'
```

### Download an attachment
Retrieve a specific file from a message to process or store locally.
```
lemma connectors operations execute gmail messages_attachments_get -d '{"payload": {"user_id": "me", "message_id": "187c26b4f5e", "id": "ANGjdJ8e"}}'
```

## Tips
- `lemma connectors operations search gmail <query>` — find more operations
- `lemma connectors operations details gmail <OPERATION>` — see full input schema