# Canva

Canva is a drag-and-drop design platform used by marketers, content creators, and businesses to create social media graphics, presentations, and marketing materials with templates and a vast library of elements.

**Auth config name:** `canva`

## Common Tasks

### Import an asset from a public URL
Use when you have a publicly accessible image, video, or PDF you want to add to the user’s Canva uploads.
```
lemma connectors operations execute canva CANVA_CREATE_URL_ASSET_UPLOAD_JOB -d '{"payload":{"url":"https://cdn.example.com/hero-banner.png","name":"Hero Banner"}}'
```

### Check an asset upload job status
Use after initiating any asset upload to learn when the file is ready and get its final asset ID.
```
lemma connectors operations execute canva CANVA_FETCH_ASSET_UPLOAD_JOB_STATUS -d '{"payload":{"jobId":"job-a1b2c3d4"}}'
```

### Fetch design metadata and access links
Use when you need the owner, edit URL, view URL, or thumbnail for a specific design.
```
lemma connectors operations execute canva CANVA_FETCH_DESIGN_METADATA_AND_ACCESS_INFORMATION -d '{"payload":{"designId":"DAFVxE2qT9m"}}'
```

### Create a resized copy of a design
Use to produce a new design with different dimensions from an existing one, preserving as much content as possible.
```
lemma connectors operations execute canva CANVA_CREATE_DESIGN_RESIZE_JOB -d '{"payload":{"design_id":"DAFVxE2qT9m","design_type":"instagram_post"}}'
```

### Delete an asset by ID
Use when an asset is no longer needed and should be moved to the trash.
```
lemma connectors operations execute canva CANVA_DELETE_ASSET_BY_ID -d '{"payload":{"assetId":"A987654321"}}'
```

### List brand templates with a search term
Use when you need to find pre-designed, editable brand templates available to the user, filtered by a keyword.
```
lemma connectors operations execute canva CANVA_ACCESS_USER_SPECIFIC_BRAND_TEMPLATES_LIST -d '{"payload":{"query":"social media","sort_by":"relevance"}}'
```

## Tips
- `lemma connectors operations search canva <query>` — find more operations
- `lemma connectors operations details canva <OPERATION>` — see full input schema