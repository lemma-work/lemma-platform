# Google Chat

Google Chat is the messaging hub within Google Workspace, letting teams exchange direct messages, group chats, and topic‑based conversations in spaces. Knowledge workers, project leads, and support staff rely on it to share real‑time updates, coordinate tasks, and keep discussions organized.

**Auth config name:** `google_chat`

## Common Tasks

### Send a Chat Message
Use when you need to post a plain‑text update to a space.
```
lemma connectors operations execute google_chat GOOGLE_CHAT_CREATE_MESSAGE -d '{"payload": {"parent": "spaces/AAAABBBBCCCC", "text": "Meeting agenda attached, please review."}}'
```

### Create a Collaboration Space
Use to set up a named, persistent space for a project or team.
```
lemma connectors operations execute google_chat GOOGLE_CHAT_CREATE_SPACE -d '{"payload": {"spaceType": "SPACE", "display_name": "Q4 Marketing Sprint", "description": "Collaboration space for Q4 marketing campaign", "requestId": "550e8400-e29b-41d4-a716-446655440000"}}'
```

### Find a Direct Message
Use to retrieve the existing DM space with a specific user before sending a private message.
```
lemma connectors operations execute google_chat GOOGLE_CHAT_FIND_DIRECT_MESSAGE -d '{"payload": {"name": "users/117781234567890123456"}}'
```

### React to a Message
Use when you want to acknowledge or emphasize a message with an emoji like a fire reaction.
```
lemma connectors operations execute google_chat GOOGLE_CHAT_CREATE_REACTION -d '{"payload": {"parent": "spaces/AAAABBBB/messages/msg-xyz123", "emoji_unicode": "🔥"}}'
```

### Delete a Message
Use to remove a message that was sent in error or is no longer relevant.
```
lemma connectors operations execute google_chat GOOGLE_CHAT_DELETE_MESSAGE -d '{"payload": {"name": "spaces/AAAABBBB/messages/msg-xyz123"}}'
```

### Remove a Reaction
Use to take back an emoji reaction you previously added.
```
lemma connectors operations execute google_chat GOOGLE_CHAT_DELETE_REACTION -d '{"payload": {"name": "spaces/AAAABBBB/messages/msg-xyz123/reactions/reaction-abc"}}'
```

## Tips
- `lemma connectors operations search google_chat <query>` — find more operations
- `lemma connectors operations details google_chat <OPERATION>` — see full input schema