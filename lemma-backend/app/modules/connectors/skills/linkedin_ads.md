# Linkedin Ads

LinkedIn Ads lets you manage campaigns, analyze performance, forecast audiences, and handle lead‑gen forms from the command line. Marketers and campaign managers use it to automate reporting, targeting discovery, and supply forecasts.

**Auth config name:** `linkedin_ads`

## Common Tasks

### Get campaign performance
Pull impression, click, and cost metrics for specific campaigns over a selected date range.
```
lemma connectors operations execute linkedin_ads LINKEDIN_ADS_GET_AD_ANALYTICS -d '{"payload": {"q": "analytics", "pivot": "CAMPAIGN", "fields": "impressions,clicks,costInLocalCurrency", "accounts": ["urn:li:sponsoredAccount:525410360"], "campaigns": ["urn:li:sponsoredCampaign:1234567"], "dateRange": {"start": "2025-03-01", "end": "2025-03-31"}}}'
```

### Estimate audience size
Forecast how many LinkedIn members match a targeting combination before you launch a campaign.
```
lemma connectors operations execute linkedin_ads LINKEDIN_ADS_GET_AUDIENCE_COUNTS -d '{"payload": {"targeting_criteria": "urn%3Ali%3AadTargetingFacet%3Alocations%3A101165590%26facet%3Durn%3Ali%3AadTargetingFacet%3Aseniorities%3A3"}}'
```

### Get supply forecasts
Predict delivery, spend, and reach for a campaign with a daily budget and bid strategy.
```
lemma connectors operations execute linkedin_ads LINKEDIN_ADS_GET_SUPPLY_FORECASTS -d '{"payload": {"account": "urn:li:sponsoredAccount:525410360", "campaign": "urn:li:sponsoredCampaign:2345678", "timeRange": {"start": 1740787200000, "end": 1743465600000}, "campaignType": "SPONSORED_UPDATES", "dailyBudget": {"amount": "50", "currency": "USD"}, "totalBudget": {"amount": "1000", "currency": "USD"}}}'
```

### Retrieve campaign group details
Check the budget, status, and schedule of an existing campaign group.
```
lemma connectors operations execute linkedin_ads LINKEDIN_ADS_GET_CAMPAIGN_GROUP -d '{"payload": {"ad_account_id": "525410360", "ad_campaign_group_id": "123456"}}'
```

### Search targeting entities
Find specific industries, skills, or job titles to refine your ad targeting.
```
lemma connectors operations execute linkedin_ads LINKEDIN_ADS_GET_TARGETING_ENTITIES -d '{"payload": {"q": "typeahead", "facet": "urn:li:adTargetingFacet:industries", "query": "software development", "locale_country": "US", "locale_language": "en"}}'
```

### Get lead form details
Retrieve the configuration and questions of a lead gen form for auditing or field mapping.
```
lemma connectors operations execute linkedin_ads LINKEDIN_ADS_GET_LEAD_FORM -d '{"payload": {"form_id": "1234567"}}'
```

## Tips
- `lemma connectors operations search linkedin_ads <query>` — find more operations
- `lemma connectors operations details linkedin_ads <OPERATION>` — see full input schema