# Google Calendar

Manage calendars, events, sharing permissions, and calendar list subscriptions for personal and team scheduling.

**Auth config name:** `google_calendar`

## Common Tasks

### Grant read-only access to a user
Share a calendar with someone so they can see your events without editing rights.
```
lemma connectors operations execute google_calendar GOOGLECALENDAR_ACL_INSERT -d '{"payload": {"calendar_id": "primary", "role": "reader", "scope": {"type": "user", "value": "alex.johnson@example.com"}, "send_notifications": false}}'
```

### List all ACL rules
Retrieve every sharing permission on a calendar to audit who has access and at what level.
```
lemma connectors operations execute google_calendar GOOGLECALENDAR_ACL_LIST -d '{"payload": {"calendarId": "primary"}}'
```

### Remove a user’s access
Revoke someone’s permission when they no longer need to see a shared calendar.
```
lemma connectors operations execute google_calendar GOOGLECALENDAR_ACL_DELETE -d '{"payload": {"calendar_id": "primary", "rule_id": "user:alex.johnson@example.com"}}'
```

### Add a calendar to your list
Make an existing secondary calendar appear in your UI after it has been created or shared with you.
```
lemma connectors operations execute google_calendar GOOGLECALENDAR_CALENDAR_LIST_INSERT -d '{"payload": {"id": "team-123@group.calendar.google.com", "hidden": false, "selected": true, "summaryOverride": "Team Projects"}}'
```

### Hide a calendar from your list
Temporarily remove a calendar from your sidebar without deleting it or unsubscribing.
```
lemma connectors operations execute google_calendar GOOGLECALENDAR_CALENDAR_LIST_PATCH -d '{"payload": {"calendar_id": "team-123@group.calendar.google.com", "hidden": true}}'
```

### Change a calendar’s color
Set a distinct background and foreground color for a calendar to match your team’s visual style.
```
lemma connectors operations execute google_calendar GOOGLECALENDAR_CALENDAR_LIST_PATCH -d '{"payload": {"calendar_id": "team-123@group.calendar.google.com", "backgroundColor": "#7bd148", "foregroundColor": "#ffffff"}}'
```

### Create events in bulk
Insert, update, or delete up to 1000 events in a single request to save time during migrations or large scheduling pushes.
```
lemma connectors operations execute google_calendar GOOGLECALENDAR_BATCH_EVENTS -d '{"payload": {"fail_fast": false, "operations": [{"method": "POST", "path": "/calendar/v3/calendars/primary/events", "body": {"summary": "Product launch review", "start": {"dateTime": "2025-06-02T09:00:00-04:00", "timeZone": "America/New_York"}, "end": {"dateTime": "2025-06-02T10:30:00-04:00", "timeZone": "America/New_York"}}}]}}'
```

## Tips
- `lemma connectors operations search google_calendar <query>` — find more operations
- `lemma connectors operations details google_calendar <OPERATION>` — see full input schema