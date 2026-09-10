# Slack

Slack is a business messaging platform for real-time team chat, channels, and direct notifications. Product, engineering, and support teams use it to coordinate projects, share alerts, and search conversation history.

**Auth config name:** `slack`

## Common Tasks

### Post a message to a channel
Send a project update or automated alert to a specific channel so the team stays aligned.
```
lemma connectors operations execute slack chat.postMessage --json '{"payload": {"channel": "C1234567890", "text": "Deployment v2.4.1 is now complete across all production nodes. All systems are green and monitoring is enabled."}}'
```

### List public channels
Find the channel ID for a team room or project channel to reference in later messages.
```
lemma connectors operations execute slack conversations.list --json '{"payload": {"limit": "50", "types": "public_channel", "exclude_archived": "true"}}'
```

### Get user details
Verify a teammate’s display name, timezone, or status before sending a direct message.
```
lemma connectors operations execute slack users.info --json '{"payload": {"user": "U1234567890", "include_locale": "true"}}'
```

### Add a reaction to a message
Acknowledge or mark a message as resolved without adding extra noise to the thread.
```
lemma connectors operations execute slack reactions.add --json '{"payload": {"channel": "C1234567890", "timestamp": "1718723400.123456", "name": "white_check_mark"}}'
```

### Upload a file snippet
Share a log, code block, or text file directly to a channel for fast debugging.
```
lemma connectors operations execute slack files.upload --json '{"payload": {"channels": "C1234567890", "content": "Error: connection timeout at /api/v1/health after 30 seconds on host prod-web-03", "filename": "health_error_log.txt", "title": "Health check timeout log from prod-web-03"}}'
```

### Find a user by email
Map an email address to a Slack member ID for mentions or direct messages.
```
lemma connectors operations execute slack users.lookupByEmail --json '{"payload": {"email": "jane.smith@example.com"}}'
```

### Update a sent message
Correct a typo or refresh a status message in place without cluttering the channel.
```
lemma connectors operations execute slack chat.update --json '{"payload": {"channel": "C1234567890", "ts": "1718723400.123456", "text": "Deployment v2.4.1 is complete. All systems green — rollback disabled and dashboards are live."}}'
```

## Tips
- `lemma connectors operations search slack <query>` — find more operations
- `lemma connectors operations details slack <OPERATION>` — see full input schema

---