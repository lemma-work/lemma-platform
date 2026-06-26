# Microsoft Teams

Microsoft Teams is a collaboration hub used by hybrid and remote teams to chat, meet, and share files. Users interact with Lemma assistants by sending direct messages or @mentioning the bot in channels.

**Auth config name:** `teams`

## Common Tasks

### Send a message to a channel
Use this when the assistant needs to broadcast an update or alert to a specific team channel.
```
lemma connectors operations execute teams send_channel_message --json '{"payload": {"team_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "channel_id": "19:4a5b6c7d@thread.tacv2", "content": "The daily sales report is ready for review."}}'
```

### Send a direct message
Privately notify a teammate with time-sensitive information or a personal reminder.
```
lemma connectors operations execute teams send_direct_message --json '{"payload": {"user_email": "priya.patel@contoso.com", "content": "Hey Priya, the Q3 projections have been updated in the shared dashboard."}}'
```

### List channels in a team
Retrieve all channels for a team before you post, so you can target the correct channel.
```
lemma connectors operations execute teams list_channels --json '{"payload": {"team_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}'
```

### Reply to a message
Respond inline to a specific message to keep the conversation threaded.
```
lemma connectors operations execute teams reply_to_message --json '{"payload": {"message_id": "1623456789012", "channel_id": "19:4a5b6c7d@thread.tacv2", "content": "Thanks for flagging this, Maria. I’ve created a ticket #TKT-409."}}'
```

### Add a reaction to a message
Quickly acknowledge a message or express sentiment without writing a full reply.
```
lemma connectors operations execute teams add_reaction --json '{"payload": {"message_id": "1623456789012", "channel_id": "19:4a5b6c7d@thread.tacv2", "reaction": "👍"}}'
```

### Upload a file to a channel
Share a document, image, or report directly within a channel conversation.
```
lemma connectors operations execute teams upload_file --json '{"payload": {"team_id": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", "channel_id": "19:4a5b6c7d@thread.tacv2", "file_path": "/data/reports/monthly_summary.pdf", "comment": "Here’s the monthly summary for the marketing team."}}'
```

## Tips
- `lemma connectors operations search teams <query>` — find more operations
- `lemma connectors operations details teams <OPERATION>` — see full input schema