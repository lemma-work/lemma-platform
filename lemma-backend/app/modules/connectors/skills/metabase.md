# Metabase

Metabase is an open-source business intelligence tool that lets you ask questions about your data and visualize answers as charts, graphs, and dashboards. Data analysts, product managers, and operations teams use it to explore databases, build reports, and share insights without writing SQL.

**Auth config name:** `metabase`

## Common Tasks

### Run a saved question (card) and get results
When you need to execute an existing card with optional filters and bypass the cache for real‑time data.
```
lemma connectors operations execute metabase METABASE_CREATE_CARD_QUERY1 -d '{"payload": {"card_id": 42, "parameters": [{"type":"category","value":"Enterprise","target":["variable",["template-tag","plan"]]}], "ignore_cache": true}}'
```

### Run a pivot table query on a card
When you must execute a card’s query and return results formatted for pivot table visualization, often for cross‑tab summaries.
```
lemma connectors operations execute metabase METABASE_CREATE_CARD_PIVOT_QUERY -d '{"payload": {"card_id": 42, "parameters": [{"type":"date/all-options","value":"2025-01-01~2025-03-31","target":["dimension",["field",78,null]]}], "ignore_cache": false}}'
```

### Bookmark a dashboard, question, or collection
When a user wants to pin a frequently accessed item for quick access from the bookmarks menu.
```
lemma connectors operations execute metabase METABASE_CREATE_BOOKMARK -d '{"payload": {"id": 123, "model": "dashboard"}}'
```

### Duplicate an existing dashboard
When you need a copy of a dashboard—perhaps to create a variant for a different team or time period, with a new name and location.
```
lemma connectors operations execute metabase METABASE_CREATE_DASHBOARD_COPY -d '{"payload": {"from_dashboard_id": 10, "name": "Sales Dashboard - Q4 2025 Copy", "collection_id": 3, "is_deep_copy": false}}'
```

### Create a brand‑new dashboard in a collection
When you need to build a dashboard from scratch, specifying its name, cards layout, and ownership.
```
lemma connectors operations execute metabase METABASE_CREATE_DASHBOARD_SAVE_COLLECTION -d '{"payload": {"name": "Marketing KPIs", "creator_id": 1, "dashcards": [{"card_id": 55, "col": 0, "row": 0, "size_x": 4, "size_y": 3}], "width": "full", "archived": false}}'
```

### Search autocomplete values for a dashboard or card parameter
When editing a dashboard or question, you need to fetch matching field values for a filter dropdown based on a partial query.
```
lemma connectors operations execute metabase METABASE_CREATE_DATASET_PARAMETER_SEARCH -d '{"payload": {"query": "Acme", "field_ids": [78, 102], "parameter": {"id": "param_abc123", "type": "string/="}}}'
```

## Tips
- `lemma connectors operations search metabase <query>` — find more operations
- `lemma connectors operations details metabase <OPERATION>` — see full input schema