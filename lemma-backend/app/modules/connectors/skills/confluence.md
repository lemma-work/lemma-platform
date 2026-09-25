# Confluence

Confluence is a collaborative documentation and knowledge management platform used by engineering, product, and operations teams to create, organize, and discuss project wikis, meeting notes, and specs.

**Auth config name:** `confluence`

## Common Tasks

### Create a new page
Use when you need to draft a new document in a specific space, optionally under a parent page.
```
lemma connectors operations execute confluence create_page -d '{"payload": {"spaceKey": "DEV", "title": "Sprint 42 Retrospective", "content": "<h2>What went well</h2><ul><li>Shipped the search feature</li><li>No critical bugs</li></ul>", "parentId": "876543"}}'
```

### Update an existing page
Use when a page needs its title or body content changed, passing the current version number to avoid conflicts.
```
lemma connectors operations execute confluence update_page -d '{"payload": {"pageId": "987654", "version": 4, "title": "Sprint 42 Retrospective — Final", "content": "<h2>What went well</h2><p>Search feature delivered on 2025-04-10.</p>"}}'
```

### Retrieve a page by ID
Use when you need the full page content, including version and space information, for referencing or editing.
```
lemma connectors operations execute confluence get_page_by_id -d '{"payload": {"pageId": "987654"}}'
```

### Search for pages using CQL
Use when you need to find pages across spaces with keywords, date ranges, or other metadata.
```
lemma connectors operations execute confluence search_pages -d '{"payload": {"cql": "title ~ \"retrospective\" and space = DEV and created >= 2025-04-01", "limit": 15}}'
```

### Add a comment to a page
Use when you need to post feedback, a question, or a status update on an existing page.
```
lemma connectors operations execute confluence add_comment -d '{"payload": {"pageId": "987654", "content": "<p>Action items look good — will review the timeline by EOD.</p>", "authorEmail": "anukul@lemma.work"}}'
```

### List spaces in the instance
Use when you need an overview of all available spaces to decide where to create or locate a page.
```
lemma connectors operations execute confluence list_spaces -d '{"payload": {"limit": 25}}'
```

## Tips
- `lemma connectors operations search confluence <query>` — find more operations
- `lemma connectors operations details confluence <OPERATION>` — see full input schema