# Google Meet
Google Meet is a secure video conferencing platform that integrates with Google Workspace, facilitating remote meetings, screen sharing, and live chat. Teams widely use it to host ad-hoc or scheduled meetings, record sessions, and automatically generate transcripts for later review.

**Auth config name:** `googlemeet`

## Common Tasks

### Create a new meeting space
Use when you need to quickly generate a reusable Meet link without attaching it to a calendar event.
```
lemma connectors operations execute googlemeet GOOGLEMEET_CREATE_MEET -d '{"payload": {"config": {"accessType": "OPEN"}}}'
```

### Retrieve details of a meeting space
Use to get the meeting URI, dial-in code, and configuration after creation or before sharing the link.
```
lemma connectors operations execute googlemeet GOOGLEMEET_GET_MEET -d '{"payload": {"space_name": "jQCFfuBOdN5z"}}'
```

### End an active conference
Use when you, as the organizer, need to forcibly terminate an ongoing meeting in a space.
```
lemma connectors operations execute googlemeet GOOGLEMEET_END_ACTIVE_CONFERENCE -d '{"payload": {"space_name": "spaces/jQCFfuBOdN5z"}}'
```

### List recent conference records
Use to review a paginated list of past meetings, filtering by start time range.
```
lemma connectors operations execute googlemeet GOOGLEMEET_LIST_CONFERENCE_RECORDS -d '{"payload": {"filter": "start_time >= \"2025-02-15T00:00:00Z\"", "page_size": 10}}'
```

### Get a specific conference record
Use when you have a conference record ID and need full meeting details such as start/end times and space name.
```
lemma connectors operations execute googlemeet GOOGLEMEET_GET_CONFERENCE_RECORD_BY_NAME -d '{"payload": {"name": "conferenceRecords/abc123-def456"}}'
```

### List participants of a conference
Use to fetch all attendees, their join/leave timestamps, and display names for a recorded meeting.
```
lemma connectors operations execute googlemeet GOOGLEMEET_LIST_PARTICIPANTS -d '{"payload": {"parent": "conferenceRecords/abc123-def456", "page_size": 20}}'
```

### Get recordings from a conference
Use to retrieve saved MP4 recordings once a meeting ends and recording was enabled by the organizer.
```
lemma connectors operations execute googlemeet GOOGLEMEET_GET_RECORDINGS_BY_CONFERENCE_RECORD_ID -d '{"payload": {"conference_record_id": "abc123-def456"}}'
```

## Tips
- `lemma connectors operations search googlemeet <query>` — find more operations
- `lemma connectors operations details googlemeet <OPERATION>` — see full input schema