# Googleforms

Google Forms is a survey administration tool used by teams to create and share online forms, collect responses, and analyze data. Common users include HR, marketing, event organizers, and educators.

**Auth config name:** `googleforms`

## Common Tasks

### Create a new blank form
Use when you need to start a new survey, feedback form, or registration.
```
lemma connectors operations execute googleforms GOOGLEFORMS_CREATE_FORM -d '{"payload": {"info": {"title": "Employee Engagement Survey Q3"}}}'
```

### Get form structure and metadata
Retrieve the full form definition—title, items, settings—to review or before making updates.
```
lemma connectors operations execute googleforms GOOGLEFORMS_GET_FORM -d '{"payload": {"formId": "1A2b3C4d5E6f7G8h9I0j"}}'
```

### Add a multiple-choice question and update the description
Modify an existing form’s content, such as inserting a new question after the first item.
```
lemma connectors operations execute googleforms GOOGLEFORMS_BATCH_UPDATE_FORM -d '{"payload": {"formId": "1A2b3C4d5E6f7G8h9I0j", "requests": [{"createItem": {"item": {"title": "Which department do you work in?", "questionItem": {"question": {"choiceQuestion": {"type": "RADIO", "options": [{"value": "Engineering"}, {"value": "Marketing"}, {"value": "Sales"}]}}}}}, "location": {"index": 1}}]}}'
```

### Publish a form and open it for responses
Make a draft form live or re-open responses after a pause.
```
lemma connectors operations execute googleforms GOOGLEFORMS_SET_PUBLISH_SETTINGS -d '{"payload": {"form_id": "1A2b3C4d5E6f7G8h9I0j", "publishSettings": {"publishState": "PUBLISHED", "acceptingResponses": true}}}'
```

### List recent form submissions
Fetch all responses, optionally filtered by date, to analyze or export data.
```
lemma connectors operations execute googleforms GOOGLEFORMS_LIST_RESPONSES -d '{"payload": {"form_id": "1A2b3C4d5E6f7G8h9I0j", "filter": "timestamp > 2025-03-01T00:00:00Z", "page_size": 100}}'
```

### Retrieve a specific response by ID
Drill into a single submission when you need the exact answers from a respondent.
```
lemma connectors operations execute googleforms GOOGLEFORMS_GET_RESPONSE -d '{"payload": {"form_id": "1A2b3C4d5E6f7G8h9I0j", "response_id": "ACYDBNhx5uLm9vNq3zrK8tWp1X"}}'
```

## Tips
- `lemma connectors operations search googleforms <query>` — find more operations
- `lemma connectors operations details googleforms <OPERATION>` — see full input schema