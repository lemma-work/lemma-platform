# Google Docs

Google Docs is a cloud-based word processor that individuals and teams use to create, edit, and collaborate on documents in real time. It’s widely adopted for drafting reports, meeting notes, project plans, and shared content where version history and simultaneous editing are essential.

**Auth config name:** `google_docs`

## Common Tasks

### Create a document with initial text
Use this to generate a new document and pre-fill it with content like a meeting agenda or project description.
```
lemma connectors operations execute google_docs GOOGLEDOCS_CREATE_DOCUMENT -d '{"payload": {"title": "Q2 Planning Session Notes", "text": "Meeting agenda: 1. Review OKRs 2. Resource allocation"}}'
```

### Create a document from Markdown
Use when you have structured content in Markdown—like a blog draft or README—and want a rich Google Doc instantly.
```
lemma connectors operations execute google_docs GOOGLEDOCS_CREATE_DOCUMENT_MARKDOWN -d '{"payload": {"title": "Product Launch Checklist", "markdown_text": "# Launch Plan\n\n- Finalize copy\n- Test payment flow\n- Announce on 2025-05-01"}}'
```

### Copy an existing document
Use to duplicate a template or create a new document based on an existing one, for instance a contract or onboarding guide.
```
lemma connectors operations execute google_docs GOOGLEDOCS_COPY_DOCUMENT -d '{"payload": {"document_id": "1aXk9zL2wV5bQ8mP3eR7hN4tY6uI0o", "title": "Onboarding Guide – Ana Costa", "include_shared_drives": true}}'
```

### Add a header with text
Use to insert a default header into a document and immediately populate it with company branding or chapter titles.
```
lemma connectors operations execute google_docs GOOGLEDOCS_CREATE_HEADER -d '{"payload": {"documentId": "1aXk9zL2wV5bQ8mP3eR7hN4tY6uI0o", "type": "DEFAULT", "text": "Confidential – Acme Corp"}}'
```

### Create a footer
Use to add a standard footer (e.g., page numbers or confidentiality notice) to the whole document or a specific section.
```
lemma connectors operations execute google_docs GOOGLEDOCS_CREATE_FOOTER -d '{"payload": {"document_id": "1aXk9zL2wV5bQ8mP3eR7hN4tY6uI0o", "type": "DEFAULT"}}'
```

### Format a block of text as bullet points
Use to turn existing paragraphs into a bulleted list, for example turning plain-text meeting action items into a scannable list.
```
lemma connectors operations execute google_docs GOOGLEDOCS_CREATE_PARAGRAPH_BULLETS -d '{"payload": {"document_id": "1aXk9zL2wV5bQ8mP3eR7hN4tY6uI0o", "createParagraphBullets": {"range": {"startIndex": 1, "endIndex": 267}}}}'
```

### Delete a specific range of content
Use to remove a section, paragraph, or text run that is no longer needed, such as outdated instructions or a signature block.
```
lemma connectors operations execute google_docs GOOGLEDOCS_DELETE_CONTENT_RANGE -d '{"payload": {"document_id": "1aXk9zL2wV5bQ8mP3eR7hN4tY6uI0o", "range": {"startIndex": 120, "endIndex": 210}}}'
```

## Tips
- `lemma connectors operations search google_docs <query>` — find more operations
- `lemma connectors operations details google_docs <OPERATION>` — see full input schema