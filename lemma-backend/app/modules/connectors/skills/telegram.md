# Telegram
Telegram is a cloud-based messaging platform used by communities, support teams, and automated agents. With this connector your agent can send and manage messages in chats, groups, and channels using a registered bot. The bot token is stored in the credential configuration.

**Auth config name:** `telegram`

## Common Tasks

### Send a text message
Use when the agent needs to reply to a user or notify a channel with a plain text message.
```
lemma connectors operations execute telegram send_message -d '{"payload": {"chat_id": 123456789, "text": "Your order #4128 has shipped and will arrive by March 15.", "parse_mode": "MarkdownV2"}}'
```

### Send a photo with caption
When sharing a product image, screenshot, or media with a descriptive caption.
```
lemma connectors operations execute telegram send_photo -d '{"payload": {"chat_id": -1002233445566, "photo": "https://cdn.example.com/receipt-8821.png", "caption": "Payment confirmed for $87.50", "parse_mode": "HTML"}}'
```

### Pin a message in a group
To highlight an important announcement or ongoing poll at the top of a group chat.
```
lemma connectors operations execute telegram pin_message -d '{"payload": {"chat_id": -1001122334455, "message_id": 992, "disable_notification": false}}'
```

### Get basic information about a chat
When the agent needs to verify a group title, member count, or bot permissions before acting.
```
lemma connectors operations execute telegram get_chat -d '{"payload": {"chat_id": "@customer_support_channel"}}'
```

### Set bot command suggestions
After deploying a new feature, update the list of commands shown to users when they type `/`.
```
lemma connectors operations execute telegram set_my_commands -d '{"payload": {"commands": [{"command": "track", "description": "Track your shipment"}, {"command": "cancel", "description": "Cancel an order"}], "scope": {"type": "default"}}}'
```

### Remove a member from a group
When the moderation policy requires kicking a user after a violation.
```
lemma connectors operations execute telegram ban_chat_member -d '{"payload": {"chat_id": -1009988776655, "user_id": 981234567, "until_date": 0}}'
```

### Forward a message to another chat
To escalate a customer inquiry from a private chat to the support team’s group.
```
lemma connectors operations execute telegram forward_message -d '{"payload": {"chat_id": -1005566778899, "from_chat_id": 445566778, "message_id": 1173}}'
```

## Tips
- `lemma connectors operations search telegram <query>` — find more operations
- `lemma connectors operations details telegram <OPERATION>` — see full input schema