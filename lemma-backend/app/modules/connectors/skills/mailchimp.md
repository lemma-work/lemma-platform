# Mailchimp

Mailchimp is an email marketing and automation platform used by businesses to create campaigns, manage contacts, and track engagement. Marketing teams and developers use it to send newsletters, automate abandoned cart emails, and sync customer data.

**Auth config name:** `mailchimp`

## Common Tasks

### Add a subscriber to an audience
Use this when a new contact joins your list, to sync their email, name, and tags.
```
lemma connectors operations execute mailchimp MAILCHIMP_ADD_CONTACT_TO_AUDIENCE -d '{"payload": {"audience_id": "abc123def456", "email_channel": {"email_address": "jane.doe@example.com"}, "merge_fields": {"FNAME": "Jane", "LNAME": "Doe"}, "tags": ["new-customer"]}}'
```

### Create a regular email campaign
Start a new campaign when you’re ready to send a newsletter or promotional email.
```
lemma connectors operations execute mailchimp MAILCHIMP_ADD_CAMPAIGN -d '{"payload": {"type": "regular", "settings__title": "April Newsletter", "settings__reply__to": "hello@janesstore.com", "settings__to_name": "Friend", "tracking__opens": true}}'
```

### Record a custom event for a subscriber
Track on-site behavior like purchases or signups to power automations and segmentation.
```
lemma connectors operations execute mailchimp MAILCHIMP_ADD_EVENT -d '{"payload": {"name": "purchased", "list_id": "abc123def456", "subscriber_hash": "8d2c4f6b9a1e3f7c0d5a8b2e4f6c8a0d", "occurred_at": "2025-04-01T14:30:00Z", "properties": {"product": "Green Hoodie", "value": 49.99}}}'
```

### Launch an abandoned cart automation
Create a classic automation that triggers when a shopper leaves items behind (requires a connected store).
```
lemma connectors operations execute mailchimp MAILCHIMP_ADD_AUTOMATION -d '{"payload": {"recipients__list__id": "list123abc", "recipients__store__id": "store456def", "settings__reply__to": "support@example.com", "settings__from__name": "Shop Team", "trigger__settings__workflow__type": "abandonedCart"}}'
```

### Connect a website for tracking
Add a connected site to get the Mailchimp tracking script for page visits and pop-up forms.
```
lemma connectors operations execute mailchimp MAILCHIMP_ADD_CONNECTED_SITE -d '{"payload": {"domain": "example.com", "foreign_id": "site789ghi"}}'
```

### Upload an image to File Manager
Store an image to use later in campaigns or signup forms.
```
lemma connectors operations execute mailchimp MAILCHIMP_ADD_FILE -d '{"payload": {"name": "transparent-pixel.png", "file_data": "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII="}}'
```

## Tips
- `lemma connectors operations search mailchimp <query>` — find more operations
- `lemma connectors operations details mailchimp <OPERATION>` — see full input schema