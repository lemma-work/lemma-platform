# Gmail

Connect to Gmail to search, read, organize, and compose email directly from the command line. Used by operators who want to automate inbox workflows without leaving the terminal.

**Auth config name:** `gmail`

## Common Tasks

### Search and fetch emails
Retrieve a filtered list of recent emails, e.g., invoices from a specific sender.
```
lemma connectors operations execute gmail GMAIL_FETCH_EMAILS -d '{"payload": {"query": "from:anukul@lemma.work subject:invoice", "user_id": "me", "max_results": 20}}'
```

### Get full message details by ID
Fetch the complete content (headers, body) of a specific email using its 16‑character hex ID.
```
lemma connectors operations execute gmail GMAIL_FETCH_MESSAGE_BY_MESSAGE_ID -d '{"payload": {"message_id": "18bf77729bcb3a44", "user_id": "me", "format": "full"}}'
```

### Send an email with attachments
`attachment` takes one pod file reference, or a list of them. Each file is read with your own access and keeps its name and type.
```
lemma connectors operations execute gmail GMAIL_SEND_EMAIL -d '{"recipient_email": "anukul@lemma.work", "subject": "Q3 report", "body": "Report and data attached."}' --attach attachment=/me/reports/q3.pdf --attach attachment=/me/reports/q3.csv
```
From an agent or function: `"attachment": [{"pod_path": "/me/reports/q3.pdf"}, {"pod_path": "/me/reports/q3.csv"}]`. `{"file_id": "..."}` works as well.

### Compose an email draft
Create a draft when you need to prepare a reply without sending immediately.
```
lemma connectors operations execute gmail GMAIL_CREATE_EMAIL_DRAFT -d '{"payload": {"to": "anukul@lemma.work", "subject": "Re: Design mockups", "body": "Hey Jane, here are the mockups -- let me know your thoughts.", "is_html": false}}'
```

### Create a new label to categorize newsletters
Add a reusable label to organize incoming mail; returns a label ID like `Label_456` for later commands.
```
lemma connectors operations execute gmail GMAIL_CREATE_LABEL -d '{"payload": {"label_name": "Newsletters", "text_color": "#ffffff", "background_color": "#16a765"}}'
```

### Apply a label to an email
Tag a single message with the “Newsletters” label (use the ID from the previous step) to keep your inbox organized.
```
lemma connectors operations execute gmail GMAIL_ADD_LABEL_TO_EMAIL -d '{"payload": {"message_id": "18bf77729bcb3a44", "add_label_ids": ["Label_456"]}}'
```

### Archive or mark messages as read
Remove the INBOX label from one or more messages (batch modify) to archive them instantly.
```
lemma connectors operations execute gmail GMAIL_BATCH_MODIFY_MESSAGES -d '{"payload": {"userId": "me", "messageIds": ["18bf77729bcb3a44", "1a2b3c4d5e6f7g8h"], "removeLabelIds": ["INBOX"]}}'
```

### Create a filter to auto‑label incoming mail
Automatically apply the “Newsletters” label to future emails from a specific address.
```
lemma connectors operations execute gmail GMAIL_CREATE_FILTER -d '{"payload": {"criteria": {"from": "anukul@lemma.work"}, "action": {"addLabelIds": ["Label_456"]}}}'
```

## Tips
- `lemma connectors operations search gmail <query>` — find more operations
- `lemma connectors operations details gmail <OPERATION>` — see full input schema