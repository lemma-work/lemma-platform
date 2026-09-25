# Googlecontacts

Access and manage Google Contacts data via the Google People API. Ideal for anyone who needs to create, read, update, or delete contacts programmatically.

**Auth config name:** `googlecontacts`

## Common Tasks

### Create a new contact
Use when you need to add a person with their name, email, and phone number to the user’s Google Contacts.
```
lemma connectors operations execute googlecontacts GOOGLECONTACTS_CREATE_CONTACT -d '{"payload": {"names": [{"givenName": "Alice", "familyName": "Johnson"}], "emailAddresses": [{"value": "alice.johnson@example.com"}], "phoneNumbers": [{"value": "+1-555-123-4567"}]}}'
```

### Retrieve a single contact
Use when you know the resource name and need the full profile (names, emails, phones) of a specific contact.
```
lemma connectors operations execute googlecontacts GOOGLECONTACTS_GET_PERSON -d '{"payload": {"resourceName": "people/c9876543210987654321", "personFields": "names,emailAddresses,phoneNumbers"}}'
```

### List all contacts
Use to fetch a paginated list of the user’s contacts showing names and email addresses, returning up to 50 per page.
```
lemma connectors operations execute googlecontacts GOOGLECONTACTS_LIST_CONNECTIONS -d '{"payload": {"page_size": 50, "person_fields": "names,emailAddresses"}}'
```

### Update an existing contact
Use when you need to change a contact’s name (or other fields) while keeping other data unchanged. Supply the resource name and an update mask.
```
lemma connectors operations execute googlecontacts GOOGLECONTACTS_BATCH_UPDATE_CONTACTS -d '{"payload": {"contacts": {"people/c9876543210987654321": {"names": [{"givenName": "Alice", "familyName": "Smith"}]}}, "readMask": "names,emailAddresses", "updateMask": "names"}}'
```

### Delete a contact
Use to permanently remove a contact from Google Contacts. The operation is irreversible.
```
lemma connectors operations execute googlecontacts GOOGLECONTACTS_DELETE_CONTACT -d '{"payload": {"resource_name": "people/c9876543210987654321"}}'
```

## Tips
- `lemma connectors operations search googlecontacts <query>` — find more operations
- `lemma connectors operations details googlecontacts <OPERATION>` — see full input schema