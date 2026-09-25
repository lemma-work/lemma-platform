# Excel

Microsoft Excel is a spreadsheet connector for data analysis, calculations, and visualization. Business users and analysts rely on it to organize data, create charts, and build tables.

**Auth config name:** `excel`

## Common Tasks

### Create a new workbook
When you need to start a fresh Excel file with predefined sheets and data.
```
lemma connectors operations execute excel EXCEL_CREATE_WORKBOOK -d '{"payload": {"path": "/Documents/Budget.xlsx", "drive_id": "me", "worksheet_names": ["Sheet1"], "worksheet_data": {"Sheet1": [["Item","Cost"],["Licenses",1200],["Salaries",3500]]}}}'
```

### Add a worksheet
When an existing workbook needs a new sheet for quarterly or summary data.
```
lemma connectors operations execute excel EXCEL_ADD_WORKSHEET -d '{"payload": {"name": "Q2 Summary", "item_id": "0123456789ABCDEF", "drive_id": "me"}}'
```

### Add a table
When you want to convert a raw range into a structured, filterable table with headers.
```
lemma connectors operations execute excel EXCEL_ADD_TABLE -d '{"payload": {"address": "A1:B10", "item_id": "0123456789ABCDEF", "drive_id": "me", "worksheet": "Sheet1", "hasHeaders": true}}'
```

### Add a row to a table
When you need to append a new data record to an existing table without overwriting it.
```
lemma connectors operations execute excel EXCEL_ADD_TABLE_ROW -d '{"payload": {"item_id": "0123456789ABCDEF", "table_id": "Table1", "values": [["Software", 850]]}}'
```

### Add a chart
When you want to visualize numeric ranges as a column chart directly in a worksheet.
```
lemma connectors operations execute excel EXCEL_ADD_CHART -d '{"payload": {"type": "ColumnClustered", "item_id": "0123456789ABCDEF", "seriesby": "Auto", "worksheet": "Sheet1", "sourcedata": "A1:B10"}}'
```

### Clear a range
When you need to remove all contents from a cell range before inserting fresh data.
```
lemma connectors operations execute excel EXCEL_CLEAR_RANGE -d '{"payload": {"address": "A1:B10", "item_id": "0123456789ABCDEF", "worksheet_id": "Sheet1", "applyTo": "Contents"}}'
```

### Apply a filter to a table column
When you need to display only rows where the cost column is greater than or equal to 1000.
```
lemma connectors operations execute excel EXCEL_APPLY_TABLE_FILTER -d '{"payload": {"item_id": "0123456789ABCDEF", "table_id": "Table1", "column_id": "1", "worksheet": "Sheet1", "criteria": {"filterOn": "custom", "criterion1": ">=1000"}}}'
```

## Tips
- `lemma connectors operations search excel <query>` — find more operations
- `lemma connectors operations details excel <OPERATION>` — see full input schema