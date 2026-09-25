# Calendly

Calendly automates meeting scheduling by letting invitees pick from your real-time availability, eliminating back-and-forth emails. Sales reps, recruiters, and customer success teams use it daily.

**Auth config name:** `calendly`

## Common Tasks

### Cancel a scheduled event
Cancel an existing appointment and notify all invitees when a meeting is no longer needed.
```
lemma connectors operations execute calendly CALENDLY_CANCEL_SCHEDULED_EVENT -d '{"payload": {"reason": "Client requested reschedule", "event_uuid": "550e8400-e29b-41d4-a716-446655440000"}}'
```

### Create a new event type
Build a reusable 1‑on‑1 meeting type (e.g., a 30‑minute qualification call) that invitees can book from your scheduling page.
```
lemma connectors operations execute calendly CALENDLY_CREATE_EVENT_TYPE -d '{"payload": {"name": "30min Discovery Call", "color": "#4287f5", "owner": "https://api.calendly.com/users/U1234567890", "active": true, "duration": 30, "locations": [{"type": "zoom"}], "description": "Quick chat to learn about your platform."}}'
```

### Create a one‑off event type
Make a temporary meeting slot outside your normal availability—perfect for a one‑time workshop or leadership AMA.
```
lemma connectors operations execute calendly CALENDLY_CREATE_ONE_OFF_EVENT_TYPE -d '{"payload": {"host": "https://api.calendly.com/users/U1234567890", "name": "Leadership AMA", "duration": 45, "timezone": "America/Chicago", "date_setting": "{\"type\":\"date_range\",\"start_date\":\"2025-07-15\",\"end_date\":\"2025-07-15\",\"start_time\":\"09:00\",\"end_time\":\"17:00\"}"}}'
```

### Create a single‑use scheduling link
Generate a one‑time booking link for an event type, expiring after the first reservation—useful for personal invitations or high‑touch outreach.
```
lemma connectors operations execute calendly CALENDLY_CREATE_SINGLE_USE_SCHEDULING_LINK -d '{"payload": {"owner": "https://api.calendly.com/event_types/ET987654", "owner_type": "EventType", "max_event_count": 1}}'
```

### Get event details
Pull the full record of a scheduled meeting to verify its status, invitees, or cancellation history.
```
lemma connectors operations execute calendly CALENDLY_GET_EVENT -d '{"payload": {"uuid": "550e8400-e29b-41d4-a716-446655440000"}}'
```

### Subscribe to webhook notifications
Register a URL to receive real‑time callbacks for bookings and cancellations across your whole organization.
```
lemma connectors operations execute calendly CALENDLY_CREATE_WEBHOOKS -d '{"payload": {"url": "https://hooks.example.com/calendly-events", "scope": "organization", "events": ["invitee.created","invitee.canceled"], "organization": "https://api.calendly.com/organizations/ORGABCD", "signing_key": "my-secret-key"}}'
```

## Tips
- `lemma connectors operations search calendly <query>` — find more operations
- `lemma connectors operations details calendly <OPERATION>` — see full input schema