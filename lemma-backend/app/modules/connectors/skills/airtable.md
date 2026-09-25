# Airtable
Airtable combines the ease of a spreadsheet with the power of a relational database, letting teams manage projects, track inventory, coordinate events, and more through customizable apps.
**Auth config name:** `airtable`

## Common Tasks

### List accessible bases
When you need to discover base IDs before working with data.
```
lemma connectors operations execute airtable AIRTABLE_LIST_BASES -d '{"payload": {}}'
```

### Retrieve a base’s schema
Use this to understand table and field names and their types before creating or reading records.
```
lemma connectors operations execute airtable AIRTABLE_GET_BASE_SCHEMA -d '{"payload": {"baseId": "appTE2RkHcOlVXh3K"}}'
```

### Add multiple records
To quickly populate a table with new rows, passing exact field values.
```
lemma connectors operations execute airtable AIRTABLE_CREATE_RECORDS -d '{"payload": {"baseId": "appTE2RkHcOlVXh3K", "tableIdOrName": "Tasks", "records": [{"fields": {"Title": "Draft proposal", "Assigned to": "lisa@example.com", "Due": "2025-05-30"}}, {"fields": {"Title": "Review contract", "Assigned to": "marcus@example.com", "Due": "2025-06-05"}}], "typecast": true}}'
```

### Create a record from a natural language description
When you want to add a single record without manually mapping fields — just describe what you need.
```
lemma connectors operations execute airtable AIRTABLE_CREATE_RECORD_FROM_NATURAL_LANGUAGE -d '{"payload": {"baseId": "appTE2RkHcOlVXh3K", "tableIdOrName": "Projects", "nl_query": "Add a project named Q3 Marketing Campaign with status Planned and start date June 1, 2025"}}'
```

### Fetch a specific record
Retrieve a known record’s details using its record ID.
```
lemma connectors operations execute airtable AIRTABLE_GET_RECORD -d '{"payload": {"baseId": "appTE2RkHcOlVXh3K", "tableIdOrName": "Tasks", "recordId": "recMx9KpLqRswV8Yz"}}'
```

### Remove a single record
Permanently delete a record when it’s no longer needed.
```
lemma connectors operations execute airtable AIRTABLE_DELETE_RECORD -d '{"payload": {"baseId": "appTE2RkHcOlVXh3K", "tableIdOrName": "Tasks", "recordId": "recMx9KpLqRswV8Yz"}}'
```

### Delete multiple records at once
Batch-remove up to 10 records in one call to clean up outdated entries.
```
lemma connectors operations execute airtable AIRTABLE_DELETE_MULTIPLE_RECORDS -d '{"payload": {"baseId": "appTE2RkHcOlVXh3K", "tableIdOrName": "Tasks", "recordIds": ["recMx9KpLqRswV8Yz", "recAb2CDeFgHIjKlM"]}}'
```

## Tips
- `lemma connectors operations search airtable <query>` — find more operations
- `lemma connectors operations details airtable <OPERATION>` — see full input schema