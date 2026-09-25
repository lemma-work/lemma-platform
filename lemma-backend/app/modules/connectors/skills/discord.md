# Discord
Discord is a real-time chat, voice, and community platform used by gamers, communities, and developers to build bots, sell premium content, and manage servers.

**Auth config name:** `discord`

## Common Tasks

### Fetch your user profile
When you need to display or verify the currently authenticated user’s account details, avatar, or linked email.
```
lemma connectors operations execute discord DISCORD_GET_MY_USER -d '{"payload": {}}'
```

### Get your OAuth2 authorization details
Use this to check which scopes your app has been granted and see token expiration and connector info.
```
lemma connectors operations execute discord DISCORD_GET_MY_OAUTH2_AUTHORIZATION -d '{"payload": {}}'
```

### Check your guild membership
Retrieve your roles, nickname, join date, and permissions inside a specific server (guild).
```
lemma connectors operations execute discord DISCORD_GET_MY_GUILD_MEMBER -d '{"payload": {"guild_id": "123456789012345678"}}'
```

### Retrieve a server’s public widget
When you need a lightweight JSON representation of a guild’s widget for external display (requires widget enabled in server settings).
```
lemma connectors operations execute discord DISCORD_GET_GUILD_WIDGET -d '{"payload": {"guild_id": "123456789012345678"}}'
```

### List your connector entitlements
Fetch the current user’s purchased or subscribed entitlements (like premium features) for a specific app.
```
lemma connectors operations execute discord DISCORD_GET_CURRENT_USER_CONNECTOR_ENTITLEMENTS -d '{"payload": {"connector_id": "987654321012345678", "limit": 25}}'
```

### Get a guild template
Look up the details and settings of a server template using its code, often used when previewing or creating a new guild from a template.
```
lemma connectors operations execute discord DISCORD_GET_GUILD_TEMPLATE -d '{"payload": {"code": "friendship-is-magic"}}'
```

## Tips
- `lemma connectors operations search discord <query>` — find more operations
- `lemma connectors operations details discord <OPERATION>` — see full input schema