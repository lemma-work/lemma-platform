# Google Sheets

Read, write, and manage spreadsheet data programmatically. Used by operations, finance, and marketing teams to automate reports, data collection, and pipeline updates.

**Auth config name:** `google_sheets`

## Common Tasks

### Create a new spreadsheet
Start a fresh workbook when you need a dedicated space for a project, report, or dataset.
```
lemma connectors operations execute google_sheets spreadsheets_create --json '{"payload": {"body": "{\"properties\": {\"title\": \"Q2 Marketing Budget\"}}", "fields": "*"}}'
```

### Read a full spreadsheet
Fetch the spreadsheet structure and optional grid data to inspect sheet names or verify setup.
```
lemma connectors operations execute google_sheets spreadsheets_get --json '{"payload": {"spreadsheet_id": "1BxiMVs0XRA5nFMjKvBdBZjgmUUqptlbs74OgvE2upms", "fields": "sheets.properties", "include_grid_data": "false"}}'
```

### Append rows to a sheet
Add new data below existing tables—ideal for logging form responses or time-series records without overwriting.
```
lemma connectors operations execute google_sheets spreadsheets_values_append --json '{"payload": {"spreadsheet_id": "1BxiMVs0XRA5nFMjKvBdBZjgmUUqptlbs74OgvE2upms", "range": "Sheet1!A1", "body": "{\"values\": [[\"Alice\", \"alice@example.com\", \"2025-04-10\"]]}", "value_input_option": "RAW", "insert_data_option": "INSERT_ROWS"}}'
```

### Update multiple ranges at once
Write or overwrite several areas in one call—perfect for refreshing a dashboard with new metrics.
```
lemma connectors operations execute google_sheets spreadsheets_values_batch_update --json '{"payload": {"spreadsheet_id": "1BxiMVs0XRA5nFMjKvBdBZjgmUUqptlbs74OgvE2upms", "body": "{\"valueInputOption\": \"RAW\", \"data\": [{\"range\": \"Sheet1!B2\", \"values\": [[\"$12,500\"]]}, {\"range\": \"Sheet2!A1\", \"values\": [[\"Avg Deal\"], [\"$3,200\"]]}]}", "fields": "*"}}'
```

### Clear values in a single range
Erase cell contents while keeping formatting intact—useful for resetting a template before the next cycle.
```
lemma connectors operations execute google_sheets spreadsheets_values_clear --json '{"payload": {"spreadsheet_id": "1BxiMVs0XRA5nFMjKvBdBZjgmUUqptlbs74OgvE2upms", "range": "Template!A2:G50", "body": "{}"}}'
```

### Read values from multiple ranges
Pull data from several non‑adjacent areas in one request—handy for building a summary from disconnected cells.
```
lemma connectors operations execute google_sheets spreadsheets_values_batch_get --json '{"payload": {"spreadsheet_id": "1BxiMVs0XRA5nFMjKvBdBZjgmUUqptlbs74OgvE2upms", "ranges": "Sheet1!A1:D10, Sheet2!A1:B5", "value_render_option": "FORMATTED_VALUE", "date_time_render_option": "FORMATTED_STRING"}}'
```

## Tips
- `lemma connectors operations search google_sheets <query>` — find more operations
- `lemma connectors operations details google_sheets <OPERATION>` — see full input schema