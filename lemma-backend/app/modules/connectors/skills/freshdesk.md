# Freshdesk
Freshdesk is a customer support platform that helps teams manage tickets, automate workflows, and share knowledge. AI agents can triage, update, and collaborate on tickets across helpdesk operations.

**Auth config name:** `freshdesk`

## Common Tasks

### Add a note to a ticket
Use this to leave an internal comment or a public reply with rich text (HTML) on an existing ticket.
```
lemma connectors operations execute freshdesk FRESHDESK_ADD_NOTE_TO_TICKET -d '{"payload": {"ticket_id": 18347, "body": "Spoke with the customer; they’ll send logs by EOD.", "private": true, "user_id": 482}}'
```

### Bulk-update multiple tickets
Change the same properties—like priority, status, or tags—across a set of tickets in a single asynchronous job.
```
lemma connectors operations execute freshdesk FRESHDESK_BULK_UPDATE_TICKETS -d '{"payload": {"ids": [105, 106, 107], "properties": {"priority": 3, "tags": ["billing_issue"]}}}'
```

### Watch a ticket
Start receiving email notifications for a ticket’s activity by adding yourself as a watcher.
```
lemma connectors operations execute freshdesk FRESHDESK_ADD_WATCHER -d '{"payload": {"ticket_id": 18347}}'
```

### Grant agent access to a ticket
Add specific agents to a ticket so they can view or work on it, even if they aren’t in the default group.
```
lemma connectors operations execute freshdesk FRESHDESK_ADD_TICKET_USER_ACCESS -d '{"payload": {"ticket_id": 18347, "user_ids": [482, 501]}}'
```

### Create a company
Register a customer organization so Freshdesk automatically links its contacts by email domain and enriches tickets with company details.
```
lemma connectors operations execute freshdesk FRESHDESK_CREATE_COMPANIES -d '{"payload": {"name": "Acme Corp", "domains": ["acmecorp.com"], "industry": "Manufacturing"}}'
```

### Create a canned response
Save a reusable reply template that agents can insert into tickets to speed up common answers.
```
lemma connectors operations execute freshdesk FRESHDESK_CREATE_CANNED_RESPONSE -d '{"payload": {"title": "Password Reset", "visibility": 1, "content_html": "<p>To reset your password, visit {{portal_url}}/forgot-password and follow the instructions.</p>", "folder_id": 12}}'
```

## Tips
- `lemma connectors operations search freshdesk <query>` — find more operations
- `lemma connectors operations details freshdesk <OPERATION>` — see full input schema