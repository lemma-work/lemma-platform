# Intercom

Intercom is a customer communication platform for live chat, email, and in‑app messaging. Support, sales, and success teams use it to engage users, manage conversations, and personalize outreach at scale.

**Auth config name:** `intercom`

## Common Tasks

### Create a contact
Add a new user or lead when they sign up or enter a funnel.
```
lemma connectors operations execute intercom INTERCOM_CREATE_CONTACT -d '{"payload": {"email": "anukul@lemma.work", "name": "Jane Doe", "role": "user", "phone": "+14155551234"}}'
```

### Add tag to a contact
Categorize a contact for segmentation (e.g., “VIP”, “trial”).
```
lemma connectors operations execute intercom INTERCOM_ADD_TAG_TO_CONTACT -d '{"payload": {"id": "98765", "contact_id": "60d5f9a2b1c3e4f5a6b7c8d9"}}'
```

### Attach contact to a company
Associate a contact with their company record for B2B tracking.
```
lemma connectors operations execute intercom INTERCOM_ATTACH_CONTACT_TO_COMPANY -d '{"payload": {"contact_id": "60d5f9a2b1c3e4f5a6b7c8d9", "company_id": "5f4c8b9c4f1c2a3b4c5d6e7f"}}'
```

### Assign a conversation
Route an open conversation to a specific support agent or team.
```
lemma connectors operations execute intercom INTERCOM_ASSIGN_CONVERSATION -d '{"payload": {"conversation_id": "64a1f2b3c4d5e6f7a8b9c0d1", "admin_id": "123456", "assignee_id": "123456"}}'
```

### Close a conversation
Resolve a conversation after the customer’s issue is solved.
```
lemma connectors operations execute intercom INTERCOM_CLOSE_CONVERSATION -d '{"payload": {"conversation_id": "64a1f2b3c4d5e6f7a8b9c0d1", "admin_id": "123456", "body": "Issue resolved after troubleshooting."}}'
```

### Create a note
Leave an internal note on a contact’s profile for the team.
```
lemma connectors operations execute intercom INTERCOM_CREATE_A_NOTE -d '{"payload": {"id": "60d5f9a2b1c3e4f5a6b7c8d9", "body": "Customer requested a call back within the hour.", "admin_id": "123456"}}'
```

### Create an article
Publish a help center article for self‑service support.
```
lemma connectors operations execute intercom INTERCOM_CREATE_AN_ARTICLE -d '{"payload": {"title": "How to reset your password", "author_id": 42, "body": "<p>Click <b>Forgot password</b> on the login screen and follow the link in your email.</p>", "state": "published", "description": "Step-by-step guide for password reset"}}'
```

## Tips
- `lemma connectors operations search intercom <query>` — find more operations
- `lemma connectors operations details intercom <OPERATION>` — see full input schema