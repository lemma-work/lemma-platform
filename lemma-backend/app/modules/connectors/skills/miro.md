# Miro

Miro is a collaborative online whiteboard where teams brainstorm, diagram workflows, and manage projects visually. Product managers, designers, and agile teams use it to align on ideas and plans.

**Auth config name:** `miro`

## Common Tasks

### Create a new board
Use this when kicking off a fresh project or sprint.
```
lemma connectors operations execute miro MIRO_CREATE_BOARD -d '{"payload": {"name": "Q4 Marketing Sprint", "description": "Sprint board for Q4 campaign planning and asset reviews"}}'
```

### Add a task card
Perfect for assigning a tracked action item with a due date and owner.
```
lemma connectors operations execute miro MIRO_CREATE_CARD_ITEM -d '{"payload": {"board_id": "3458764515789123456", "data": {"title": "Draft blog post outline", "description": "Outline the Q4 SEO blog post", "dueDate": "2025-09-20", "assigneeId": "3074457349744051200"}}}'
```

### Frame a region
Group related items visually, like all wireframes or a sprint lane.
```
lemma connectors operations execute miro MIRO_CREATE_FRAME_ITEM -d '{"payload": {"board_id": "3458764515789123456", "data": {"title": "Wireframes v2", "format": "desktop"}, "geometry": {"width": 1200, "height": 900}, "position": {"x": 0, "y": 0}}}'
```

### Connect two shapes with an arrow
Show flow between diagram nodes, like linking a step to its trigger.
```
lemma connectors operations execute miro MIRO_CREATE_CONNECTOR -d '{"payload": {"board_id": "3458764515789123456", "shape": "elbowed", "startItem": {"snapTo": "3458764515789123457"}, "endItem": {"snapTo": "3458764515789123458"}, "captions": [{"content": "triggers"}]}}'
```

### Upload a document from a URL
Attach a PDF brief, report, or spec directly from your internal repository.
```
lemma connectors operations execute miro MIRO_CREATE_DOCUMENT_ITEM -d '{"payload": {"board_id": "3458764515789123456", "data": {"url": "https://company.sharepoint.com/sites/project/brief.pdf", "title": "Project Brief"}}}'
```

### Embed a YouTube video
Place a recorded walkthrough or demo right on the board for quick reference.
```
lemma connectors operations execute miro MIRO_CREATE_EMBED_ITEM -d '{"payload": {"board_id": "3458764515789123456", "data": {"url": "https://www.youtube.com/watch?v=dQw4w9WgXcQ"}, "position": {"x": 200, "y": 400}}}'
```

### Attach a tag to an item
Classify a sticky note or card with an existing tag like “High Priority”.
```
lemma connectors operations execute miro MIRO_ATTACH_TAG_TO_ITEM -d '{"payload": {"board_id": "3458764515789123456", "item_id": "3458764515799123459", "tag_id": "3458764515789123999"}}'
```

## Tips
- `lemma connectors operations search miro <query>` — find more operations  
- `lemma connectors operations details miro <OPERATION>` — see full input schema