# Cal

Cal lets individuals and teams create shareable booking pages, sync external calendars, and manage availability — so anyone can schedule meetings without back‑and‑forth emails. It’s used by professionals, teams, and organizations for everything from one‑on‑one calls to team events.

**Auth config name:** `cal`

## Common Tasks

### Check calendar availability
When you need to see free/busy slots for a calendar before offering times.
```
lemma connectors operations execute cal CAL_CHECK_CALENDAR_VERSION2 -d '{"payload": {"calendar": "google"}}'
```

### Add an attendee to an existing booking
When an extra participant needs to join a scheduled event without creating a new booking.
```
lemma connectors operations execute cal CAL_ADD_ATTENDEE -d '{"payload": {"name": "Jane Doe", "email": "jane.doe@example.com", "timeZone": "America/Chicago", "bookingId": 482}}'
```

### Cancel a booking via UID
When a meeting is no longer needed and you have the booking’s unique identifier.
```
lemma connectors operations execute cal CAL_CANCEL_BOOKING_VIA_UID -d '{"payload": {"bookingUid": "ckz4q9f8r0000p0mn8y6e4t9s", "cancellationReason": "Schedule conflict"}}'
```

### Confirm a booking by UID
When a tentative booking needs to be finalized and locked in.
```
lemma connectors operations execute cal CAL_CONFIRM_BOOKING_BY_UID -d '{"payload": {"bookingUid": "ckz4q9f8r0000p0mn8y6e4t9s"}}'
```

### Connect a Google, Apple, or Office 365 calendar
When syncing a personal or work calendar to Cal.com so that booked events automatically appear there.
```
lemma connectors operations execute cal CAL_CONNECT_TO_CALENDAR -d '{"payload": {"calendar": "google"}}'
```

## Tips
- `lemma connectors operations search cal <query>` — find more operations
- `lemma connectors operations details cal <OPERATION>` — see full input schema