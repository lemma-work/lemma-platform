# Figma

Figma is a collaborative interface design tool used by product teams to create, prototype, and share designs. It is commonly used by designers, developers, and product managers.

**Auth config name:** `figma`

## Common Tasks

### Add a comment to a design
Use this to provide feedback on a specific file or branch without needing to open Figma.
```
lemma connectors operations execute figma FIGMA_ADD_A_COMMENT_TO_A_FILE -d '{"payload": {"message": "The hero section spacing feels inconsistent.", "file_key": "D2e3F4g5H6i7J8k9L0"}}'
```

### React to a comment
Use this to quickly acknowledge or express sentiment on an existing comment with an emoji.
```
lemma connectors operations execute figma FIGMA_ADD_A_REACTION_TO_A_COMMENT -d '{"payload": {"emoji": ":+1:", "file_key": "D2e3F4g5H6i7J8k9L0", "comment_id": "1342567890"}}'
```

### Export design assets
Use this to download UI elements as images for presentations, handoff, or documentation.
```
lemma connectors operations execute figma FIGMA_DOWNLOAD_FIGMA_IMAGES -d '{"payload": {"file_key": "D2e3F4g5H6i7J8k9L0", "images": [{"node_id": "12:34", "format": "png", "scale": 2}]}}'
```

### Extract design tokens
Use this to pull colors, typography, and spacing values defined as Figma styles and variables for use in code.
```
lemma connectors operations execute figma FIGMA_EXTRACT_DESIGN_TOKENS -d '{"payload": {"file_key": "D2e3F4g5H6i7J8k9L0", "include_variables": true, "include_local_styles": true}}'
```

### Generate Tailwind config from tokens
Use this after extracting tokens to instantly create a Tailwind CSS configuration file that reflects the design system.
```
lemma connectors operations execute figma FIGMA_DESIGN_TOKENS_TO_TAILWIND -d '{"payload": {"tokens": {"colors": {"primary-500": "#3B82F6", "secondary-500": "#8B5CF6"}, "fontFamilies": {"sans": "Inter"}}, "prefix": "brand-", "config_format": "ts", "include_font_imports": false}}'
```

### Resolve file and node IDs from a URL
Use this to discover the file key, project ID, and all node IDs inside a Figma link, which helps in subsequent API calls.
```
lemma connectors operations execute figma FIGMA_DISCOVER_FIGMA_RESOURCES -d '{"payload": {"figma_url": "https://www.figma.com/file/D2e3F4g5H6i7J8k9L0/Design-System?node-id=12:34", "max_depth": 3}}'
```

### Export prototype interactions
Use this to retrieve interactive flows and animation details from a prototype for documentation or QA.
```
lemma connectors operations execute figma FIGMA_EXTRACT_PROTOTYPE_INTERACTIONS -d '{"payload": {"file_key": "D2e3F4g5H6i7J8k9L0", "analyze_components": true, "include_animations": true}}'
```

## Tips
- `lemma connectors operations search figma <query>` — find more operations
- `lemma connectors operations details figma <OPERATION>` — see full input schema