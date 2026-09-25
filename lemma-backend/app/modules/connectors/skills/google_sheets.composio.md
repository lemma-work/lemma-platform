# Google Sheets

Google Sheets is the web-based spreadsheet platform for creating, editing, and collaborating on structured data. Finance teams, project managers, and ops professionals use it to manage budgets, track metrics, and build shared reports.

**Auth config name:** `google_sheets`

## Common Tasks

### Create a new spreadsheet
Use when you need a blank workbook for a project, budget, or tracker.
```
lemma connectors operations execute google_sheets GOOGLESHEETS_CREATE_GOOGLE_SHEET1 -d '{"payload": {"title": "Q3 Budget", "folder_id": "1KXZoZvz6q9N8KzLpMnTrStUvWxYz123"}}'
```

### Add a sheet tab to an existing spreadsheet
Add a named tab when you need a new section within a workbook, such as a monthly report or department data.
```
lemma connectors operations execute google_sheets GOOGLESHEETS_ADD_SHEET -d '{"payload": {"spreadsheet_id": "1ABC23defGHI456jklMNO789pqrSTU012vwx", "title": "Report"}}'
```

### Write values to a range
Populate or replace a block of cells in one shot, like pasting a table of product sales starting at A1.
```
lemma connectors operations execute google_sheets GOOGLESHEETS_BATCH_UPDATE -d '{"payload": {"spreadsheet_id": "1ABC23defGHI456jklMNO789pqrSTU012vwx", "sheet_name": "Sheet1", "values": [["Product", "Revenue"], ["Widget A", 1200], ["Widget B", 2300]], "first_cell_location": "A1", "value_input_option": "USER_ENTERED"}}'
```

### Read data from a cell range
Retrieve values when you need to inspect contents, such as pulling the first ten rows of a sheet for analysis.
```
lemma connectors operations execute google_sheets GOOGLESHEETS_BATCH_GET -d '{"payload": {"spreadsheet_id": "1ABC23defGHI456jklMNO789pqrSTU012vwx", "ranges": ["Sheet1!A1:B10"]}}'
```

### Clear values from a range
Wipe cell contents while keeping formatting intact—useful for resetting a section before importing fresh data.
```
lemma connectors operations execute google_sheets GOOGLESHEETS_CLEAR_VALUES -d '{"payload": {"spreadsheet_id": "1ABC23defGHI456jklMNO789pqrSTU012vwx", "range": "Sheet1!A1:B20"}}'
```

### Aggregate column data
Calculate a total, average, or count from a column, optionally filtering rows—perfect for summing revenue from a specific product.
```
lemma connectors operations execute google_sheets GOOGLESHEETS_AGGREGATE_COLUMN_DATA -d '{"payload": {"spreadsheet_id": "1ABC23defGHI456jklMNO789pqrSTU012vwx", "sheet_name": "Sheet1", "operation": "SUM", "target_column": "B", "has_header_row": true}}'
```

### Auto‑resize columns to fit content
Adjust column widths after writing long entries so all data is visible without manual dragging.
```
lemma connectors operations execute google_sheets GOOGLESHEETS_AUTO_RESIZE_DIMENSIONS -d '{"payload": {"spreadsheet_id": "1ABC23defGHI456jklMNO789pqrSTU012vwx", "sheet_name": "Sheet1", "dimension": "COLUMNS", "start_index": 0, "end_index": 2}}'
```

## Tips
- `lemma connectors operations search google_sheets <query>` — find more operations
- `lemma connectors operations details google_sheets <OPERATION>` — see full input schema