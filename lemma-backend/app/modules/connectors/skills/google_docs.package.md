# Google Docs
Google Docs is a collaborative online document editor that allows real-time editing, commenting, and version history. Content and operations teams use it for drafting reports, proposals, and process docs.

**Auth config name:** `google_docs`

## Common Tasks

### Create a blank document
When you need to start a new doc from scratch.
```
lemma connectors operations execute google_docs documents_create --json '{"payload": {"body": "{\"title\":\"Executive Summary 2025-03-15\"}", "fields": "documentId,title"}}'
```

### Fetch the latest document content
When you need to extract text and formatting for downstream processing or review.
```
lemma connectors operations execute google_docs documents_get --json '{"payload": {"document_id": "1kzI6vF5eG8hJ3mP0qRtUwXyZaBcDeFg", "fields": "body(content)"}}'
```

### Append a paragraph at the end
When you want to add new content after everything already in the document.
```
lemma connectors operations execute google_docs documents_batch_update --json '{"payload": {"document_id": "1kzI6vF5eG8hJ3mP0qRtUwXyZaBcDeFg", "body": "{\"requests\":[{\"insertText\":{\"location\":{\"index\":142},\"text\":\"\\nAction items:\\n- Review Q1 metrics\\n- Draft budget\"}}]}"}}'
```

### Replace every occurrence of a phrase
When you need to update outdated references or fix a repeated typo across the whole document.
```
lemma connectors operations execute google_docs documents_batch_update --json '{"payload": {"document_id": "1kzI6vF5eG8hJ3mP0qRtUwXyZaBcDeFg", "body": "{\"requests\":[{\"replaceAllText\":{\"containsText\":{\"text\":\"Q4\",\"matchCase\":true},\"replaceText\":\"Q5\"}}]}"}}'
```

### Insert a styled heading
When you want to add a section title with proper formatting (Heading 1) at the top of the document.
```
lemma connectors operations execute google_docs documents_batch_update --json '{"payload": {"document_id": "1kzI6vF5eG8hJ3mP0qRtUwXyZaBcDeFg", "body": "{\"requests\":[{\"insertText\":{\"location\":{\"index\":1},\"text\":\"Introduction\\n\"}},{\"updateParagraphStyle\":{\"range\":{\"startIndex\":1,\"endIndex\":14},\"paragraphStyle\":{\"namedStyleType\":\"HEADING_1\"},\"fields\":\"namedStyleType\"}}]}"}}'
```

### Turn lines into a bulleted list
When you have a set of paragraphs that should be formatted as bullet points.
```
lemma connectors operations execute google_docs documents_batch_update --json '{"payload": {"document_id": "1kzI6vF5eG8hJ3mP0qRtUwXyZaBcDeFg", "body": "{\"requests\":[{\"createParagraphBullets\":{\"range\":{\"startIndex\":30,\"endIndex\":85},\"bulletPreset\":\"BULLET_DISC_CIRCLE_SQUARE\"}}]}"}}'
```

### Add a hyperlink to a text range
When you need to turn a specific text segment into a clickable URL.
```
lemma connectors operations execute google_docs documents_batch_update --json '{"payload": {"document_id": "1kzI6vF5eG8hJ3mP0qRtUwXyZaBcDeFg", "body": "{\"requests\":[{\"updateTextStyle\":{\"range\":{\"startIndex\":10,\"endIndex\":30},\"textStyle\":{\"link\":{\"url\":\"https://www.lemma.io\"}},\"fields\":\"link\"}}]}"}}'
```

## Tips
- `lemma connectors operations search google_docs <query>` — find more operations
- `lemma connectors operations details google_docs <OPERATION>` — see full input schema
- Always set `document_id` to the target document’s ID (found in the URL).
- Use `documents_get` with `fields=body(content)` to retrieve the current end index for precise insertions.