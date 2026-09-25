# HubSpot

HubSpot is an inbound marketing, sales, and customer service platform used by marketing and sales teams to manage CRM records, automate email campaigns, and track campaign performance.  

**Auth config name:** `hubspot`

## Common Tasks

### Archive a contact
Use this to remove a stale or unqualified lead from active views without permanent deletion.  
```
lemma connectors operations execute hubspot HUBSPOT_ARCHIVE_CONTACT -d '{"payload": {"contactId": "386009987808"}}'
```

### Archive multiple deals
Use to clean up lost or outdated deals in bulk, keeping pipelines accurate.  
```
lemma connectors operations execute hubspot HUBSPOT_ARCHIVE_DEALS -d '{"payload": {"inputs": [{"id": "712345678"}, {"id": "712345679"}]}}'
```

### Archive a company
Use when a company record is no longer active or needed, moving it to the recycle bin.  
```
lemma connectors operations execute hubspot HUBSPOT_ARCHIVE_COMPANY -d '{"payload": {"companyId": "901234567"}}'
```

### Attach a form to a marketing campaign
Associate an existing form (e.g., a landing‑page signup) with a campaign to track conversions and attribution.  
```
lemma connectors operations execute hubspot HUBSPOT_ADD_ASSET_ASSOCIATION -d '{"payload": {"assetId": "12345-form", "assetType": "FORM", "campaignGuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890"}}'
```

### Archive batch of feedback submissions
Remove outdated NPS or survey responses from active reporting while preserving the data.  
```
lemma connectors operations execute hubspot HUBSPOT_ARCHIVE_BATCH_OF_FEEDBACK_SUBMISSIONS -d '{"payload": {"inputs": [{"id": "fdbk-001"}, {"id": "fdbk-002"}]}}'
```

### Archive a sent marketing email
Hide an old email you no longer want to appear in the marketing email dashboard.  
```
lemma connectors operations execute hubspot HUBSPOT_ARCHIVE_EMAIL -d '{"payload": {"emailId": "555123456"}}'
```

## Tips
- `lemma connectors operations search hubspot <query>` — find more operations  
- `lemma connectors operations details hubspot <OPERATION>` — see full input schema