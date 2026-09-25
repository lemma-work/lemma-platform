# Google Analytics
Google Analytics collects and reports website and app traffic, user behavior, and conversion data. Marketing teams, product analysts, and growth managers use it to measure performance, understand customer journeys, and optimize campaigns.

**Auth config name:** `google_analytics`

## Common Tasks

### Run a Standard Report
When you need usage data like active users by country over a specific date range, batch run reports fetches multiple GA4 reports in one request.
```
lemma connectors operations execute google_analytics GOOGLE_ANALYTICS_BATCH_RUN_REPORTS -d '{"payload": {"property": "properties/348215679", "requests": [{"dateRanges": [{"startDate": "2025-02-01", "endDate": "2025-02-28"}], "dimensions": [{"name": "country"}], "metrics": [{"name": "activeUsers"}, {"name": "sessions"}], "limit": "25"}]}}'
```

### Check Metric-Dimension Compatibility
Before running a complex report, verify that your chosen dimensions and metrics work together to avoid compatibility errors.
```
lemma connectors operations execute google_analytics GOOGLE_ANALYTICS_CHECK_COMPATIBILITY -d '{"payload": {"property": "properties/348215679", "dimensions": [{"name": "country"}, {"name": "deviceCategory"}], "metrics": [{"name": "activeUsers"}, {"name": "conversions"}]}}'
```

### Export an Audience Snapshot
When you need a list of users currently in an audience (e.g., “All Converters”) for off-platform analysis, create an audience export.
```
lemma connectors operations execute google_analytics GOOGLE_ANALYTICS_CREATE_AUDIENCE_EXPORT -d '{"payload": {"parent": "properties/348215679", "audience": "properties/348215679/audiences/1122334455", "dimensions": [{"dimensionName": "country"}, {"dimensionName": "deviceCategory"}]}}'
```

### Create an Audience List
Capture a one‑time snapshot of users in an audience and store it for later retrieval with a simple audience list.
```
lemma connectors operations execute google_analytics GOOGLE_ANALYTICS_CREATE_AUDIENCE_LIST -d '{"payload": {"parent": "properties/348215679", "audience": "properties/348215679/audiences/1122334455", "dimensions": [{"dimensionName": "country"}, {"dimensionName": "deviceCategory"}]}}'
```

### Get Attribution Settings
Inspect the current attribution model (e.g., data‑driven, last click) and lookback window configured for a property.
```
lemma connectors operations execute google_analytics GOOGLE_ANALYTICS_GET_ATTRIBUTION_SETTINGS -d '{"payload": {"name": "properties/348215679/attributionSettings"}}'
```

### Fetch Audience Details
Review membership criteria, filter clauses, and duration of a specific audience before using it in exports or reports.
```
lemma connectors operations execute google_analytics GOOGLE_ANALYTICS_GET_AUDIENCE -d '{"payload": {"audienceId": "1122334455", "propertyId": "348215679"}}'
```

## Tips
- `lemma connectors operations search google_analytics <query>` — find more operations
- `lemma connectors operations details google_analytics <OPERATION>` — see full input schema