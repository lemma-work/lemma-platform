# Slack

Slack is a team messaging platform used to share channels, direct messages, files, and real-time updates across workspaces. AI agents use it to post updates, search conversations, react to messages, and manage workspace content on behalf of users.

**Auth config name:** `slack`

## Common Tasks

### Send a message to a channel
Use this to post a text update, notification, or threaded reply to a specific channel.
```
lemma connectors operations execute slack SLACK_CHAT_POST_MESSAGE -d '{"payload": {"channel": "C1234567890", "text": "The deployment completed at 10:00 AM UTC."}}'
```

### Search messages and files
Search for recent conversations, files, or users across channels using a keyword or natural language query.
```
lemma connectors operations execute slack SLACK_ASSISTANT_SEARCH_CONTEXT -d '{"payload": {"query": "Q3 budget review", "limit": 10, "sort": "timestamp", "sort_dir": "desc"}}'
```

### React to a message with emoji
Add a thumbs-up or other emoji reaction to a specific message to acknowledge it quickly without typing a reply.
```
lemma connectors operations execute slack SLACK_ADD_REACTION_TO_AN_ITEM -d '{"payload": {"channel": "C1234567890", "timestamp": "1715420000.000000", "name": "thumbsup"}}'
```

### Archive an inactive channel
Close an inactive public or private channel so it becomes read-only and hidden from the sidebar while keeping history.
```
lemma connectors operations execute slack SLACK_ARCHIVE_CONVERSATION -d '{"payload": {"channel": "C1234567890"}}'
```

### Add a remote file reference
Share a link to an external file so it appears in Slack search and file listings with a visible preview.
```
lemma connectors operations execute slack SLACK_ADD_REMOTE_FILE -d '{"payload": {"title": "Q3 Financial Model", "external_id": "fin-model-2024", "external_url": "https://docs.google.com/spreadsheets/d/1abc123/edit"}}'
```

### Close a direct message
Remove a one-on-one or group direct message from the sidebar to keep the workspace clean without deleting message history.
```
lemma connectors operations execute slack SLACK_CLOSE_DM -d '{"payload": {"channel": "D1234567890"}}'
```

## Tips
- `lemma connectors operations search slack <query>` — find more operations
- `lemma connectors operations details slack <OPERATION>` — see full input schema