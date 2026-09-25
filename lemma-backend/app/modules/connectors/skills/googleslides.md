# Google Slides

Google Slides is a cloud-based presentation editor for creating, collaborating on, and sharing slide decks. Marketing teams, consultants, and project managers use it to build pitch decks, reports, and training materials quickly.

**Auth config name:** `googleslides`

## Common Tasks

### Create a blank presentation
Use this when you need a fresh presentation with a custom title and locale.
```
lemma connectors operations execute googleslides GOOGLESLIDES_CREATE_PRESENTATION -d '{"payload": {"title": "Q3 Sales Kickoff", "locale": "en-US"}}'
```

### Build a full deck from Markdown
Use this to turn structured Markdown text into a complete presentation—ideal for turning meeting notes or outlines into slides in seconds.
```
lemma connectors operations execute googleslides GOOGLESLIDES_CREATE_SLIDES_MARKDOWN -d '{"payload": {"title": "Weekly Sync - March 10", "markdown_text": "# Weekly Sync\n---\n## Accomplishments\n- Launched checkout flow\n-Acquired 340 beta users\n---\n## Blockers\n- Payment processor delay\n---\n## Next Steps\n- Finalize Q2 roadmap"}}'
```

### Get a slide thumbnail for a preview
Use this when you need a quick visual of a specific slide, for example to embed in a status email or dashboard.
```
lemma connectors operations execute googleslides GOOGLESLIDES_GET_PAGE_THUMBNAIL2 -d '{"payload": {"presentationId": "1A2B3C4D5E6F", "pageObjectId": "g123abc456", "thumbnailProperties": {"thumbnailSize": "LARGE"}}}'
```

### Add slides to an existing presentation with Markdown
Use this to quickly insert new content slides into a presentation you already have, without writing raw API requests.
```
lemma connectors operations execute googleslides GOOGLESLIDES_PRESENTATIONS_BATCH_UPDATE -d '{"payload": {"presentationId": "1A2B3C4D5E6F", "markdown_text": "## Q2 Targets\n- Revenue: $2.4M\n- New logos: 45\n\n## Key Initiatives\n- Enterprise tier launch\n- EMEA expansion"}}'
```

### Copy a presentation from a template
Use this to spin up a branded, pre-designed deck for a monthly report, pitch, or any repeatable document.
```
lemma connectors operations execute googleslides GOOGLESLIDES_PRESENTATIONS_COPY_FROM_TEMPLATE -d '{"payload": {"template_presentation_id": "1TemplateXYZ789", "new_title": "Monthly Client Report - March 2025", "parent_folder_id": "1FolderABC654"}}'
```

### Retrieve presentation metadata
Use this to fetch the title, slide count, or revision details after you’ve created or copied a deck.
```
lemma connectors operations execute googleslides GOOGLESLIDES_PRESENTATIONS_GET -d '{"payload": {"presentationId": "1A2B3C4D5E6F", "fields": "title,slides.pageElements"}}'
```

### Inspect a single slide’s contents
Use this when you need to examine a specific slide’s text, layout, or elements—helpful for auditing or extracting data.
```
lemma connectors operations execute googleslides GOOGLESLIDES_PRESENTATIONS_PAGES_GET -d '{"payload": {"presentationId": "1A2B3C4D5E6F", "pageObjectId": "p789xyz012"}}'
```

## Tips
- `lemma connectors operations search googleslides <query>` — find more operations
- `lemma connectors operations details googleslides <OPERATION>` — see full input schema