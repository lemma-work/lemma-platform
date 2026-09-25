# Google Ads

Google Ads lets advertisers create, target, and pay for search, display, and video ads. Marketing teams and agencies use it to reach audiences, drive conversions, and measure campaign performance at scale.

**Auth config name:** `googleads`

## Common Tasks

### List all accessible customer accounts
When you need to discover which Google Ads accounts your credentials can access before running any other operation.
```
lemma connectors operations execute googleads GOOGLEADS_LIST_ACCESSIBLE_CUSTOMERS -d '{}'
```

### Run a GAQL query for performance metrics
Pull campaign-level metrics such as clicks, impressions, and cost—ideal for reporting or monitoring.
```
lemma connectors operations execute googleads GOOGLEADS_SEARCH_STREAM_GAQL -d '{"payload": {"query": "SELECT campaign.id, campaign.name, metrics.clicks, metrics.impressions, metrics.cost_micros FROM campaign WHERE campaign.status = \u0027ENABLED\u0027"}}'
```

### Look up a campaign by exact name
Find a single campaign when you know its precise name, without scanning large lists.
```
lemma connectors operations execute googleads GOOGLEADS_GET_CAMPAIGN_BY_NAME -d '{"payload": {"name": "Summer Sale 2025"}}'
```

### Create a new customer list
Set up an email-based audience list for remarketing or Customer Match targeting.
```
lemma connectors operations execute googleads GOOGLEADS_CREATE_CUSTOMER_LIST -d '{"payload": {"name": "Newsletter Subscribers", "description": "Emails from our newsletter signup page"}}'
```

### Add contacts to a customer list
Import email addresses into an existing list to grow an audience segment. Expect a 6–12 hour delay before changes appear.
```
lemma connectors operations execute googleads GOOGLEADS_ADD_OR_REMOVE_TO_CUSTOMER_LIST -d '{"payload": {"resource_name": "customers/9876543210/userLists/111111111", "emails": ["jane.doe@example.com", "john.smith@example.com"], "operation": "create"}}'
```

### Retrieve all customer lists
List every audience list in the account to find the correct resource name before adding or removing members.
```
lemma connectors operations execute googleads GOOGLEADS_GET_CUSTOMER_LISTS -d '{}'
```

### Pause a campaign
Stop an active campaign without deleting it, preserving settings for later reactivation.
```
lemma connectors operations execute googleads GOOGLEADS_MUTATE_CAMPAIGNS -d '{"payload": {"operations": [{"update": {"resource_name": "customers/1234567890/campaigns/987654321", "status": "PAUSED"}}]}}'
```

## Tips
- `lemma connectors operations search googleads <query>` — find more operations
- `lemma connectors operations details googleads <OPERATION>` — see full input schema