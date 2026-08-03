# Google Calendar

Google Calendar is a time-management and scheduling service that lets you create and edit events, manage multiple calendars, and control sharing. This connector automates calendar list management and access control.

**Auth config name:** `google_calendar`

## Common Tasks

### List all calendars in your calendar list
Retrieve every calendar you’ve added to your account, including subscribed and shared calendars.
```
lemma connectors operations execute google_calendar calendar_list_list --json '{"payload": {"max_results": "10"}}'
```

### Get details for a specific calendar
View metadata such as summary, description, and time zone when you know the calendar’s ID.
```
lemma connectors operations execute google_calendar calendar_list_get --json '{"payload": {"calendar_id": "alex.johnson@example.com"}}'
```

### Add an existing calendar to your list
Subscribe to a public or colleague’s calendar (e.g., a team schedule) so it appears in your view.
```
lemma connectors operations execute google_calendar calendar_list_insert --json '{"payload": {"body": "{\"id\": \"team-calendar@example.com\"}"}}'
```

### Remove a calendar from your list
Unsubscribe from a calendar you no longer need, hiding it without deleting the calendar itself.
```
lemma connectors operations execute google_calendar calendar_list_delete --json '{"payload": {"calendar_id": "team-calendar@example.com"}}'
```

### List access control rules for a calendar
See who has what permission (reader, writer, owner) on a specific calendar like your primary one.
```
lemma connectors operations execute google_calendar acl_list --json '{"payload": {"calendar_id": "primary"}}'
```

### Share a calendar with a specific person
Add a permission rule, e.g., grant someone write access to your primary calendar without sending a notification.
```
lemma connectors operations execute google_calendar acl_insert --json '{"payload": {"calendar_id": "primary", "body": "{\"role\": \"writer\", \"scope\": {\"type\": \"user\", \"value\": \"brian@example.com\"}}"}}'
```

## Tips
- `lemma connectors operations search google_calendar <query>` — find more operations
- `lemma connectors operations details google_calendar <OPERATION>` — see full input schema