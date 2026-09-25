# Linear
Linear is an issue tracking and project management tool for modern software teams, offering fast, keyboard-driven workflows and tight GitHub connectors. Product managers, engineers, and designers rely on it to plan sprints, track bugs, and manage roadmaps collaboratively.

**Auth config name:** `linear`

## Common Tasks

### Create an issue
Use this command when you need to log a new task, bug, or feature request, optionally setting a priority and due date for a specific team.
```
lemma connectors operations execute linear LINEAR_CREATE_LINEAR_ISSUE -d '{"payload": {"title": "Fix login timeout bug", "team_id": "d282d590-8462-4390-8e12-abcdef123456", "priority": 2, "due_date": "2025-04-18"}}'
```

### Add a comment
Use to provide an update, ask a clarifying question, or share Markdown-formatted context on an existing issue for team visibility.
```
lemma connectors operations execute linear LINEAR_CREATE_LINEAR_COMMENT -d '{"payload": {"body": "Deployed a hotfix to staging. Please verify.", "issueId": "71bc4480-3a12-4f56-8901-abcdefabcdef"}}'
```

### Attach a file
Use to link external resources like screenshots, design files, or documents to an issue for immediate visual context.
```
lemma connectors operations execute linear LINEAR_CREATE_ATTACHMENT -d '{"payload": {"url": "https://example.com/screenshots/bug.png", "title": "Screenshot of error", "issue_id": "71bc4480-3a12-4f56-8901-abcdefabcdef", "subtitle": "Login timeout error"}}'
```

### Create a label
Use to categorize issues within a team, such as marking work as “bug,” “feature,” or “performance,” with a custom color.
```
lemma connectors operations execute linear LINEAR_CREATE_LINEAR_LABEL -d '{"payload": {"name": "performance", "color": "#F2994A", "team_id": "d282d590-8462-4390-8e12-abcdef123456", "description": "Performance-related tasks"}}'
```

### Create a project
Use when launching a new initiative that spans multiple issues, like a feature release or a complete redesign, and assign it to one or more teams.
```
lemma connectors operations execute linear LINEAR_CREATE_LINEAR_PROJECT -d '{"payload": {"name": "Q3 Mobile Redesign", "team_ids": ["d282d590-8462-4390-8e12-abcdef123456"], "start_date": "2025-07-01", "priority": 3, "description": "Redesign the mobile app for Q3 launch."}}'
```

### Add a milestone to a project
Use to mark a significant checkpoint or deliverable date within a project to track progress toward a larger goal and keep stakeholders aligned.
```
lemma connectors operations execute linear LINEAR_CREATE_PROJECT_MILESTONE -d '{"payload": {"name": "Design sprint complete", "project_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d", "target_date": "2025-07-18"}}'
```

### Post a project update
Use to share a weekly status summary, highlight recent accomplishments, and call out blockers, optionally setting a health status like “onTrack” or “atRisk.”
```
lemma connectors operations execute linear LINEAR_CREATE_PROJECT_UPDATE -d '{"payload": {"body": "Sprint 1 finished. UI mockups approved. Starting user testing next week.", "project_id": "9b1deb4d-3b7d-4bad-9bdd-2b0d7b3dcb6d", "health": "onTrack"}}'
```

## Tips
- Run `lemma connectors operations search linear <query>` to find more operations by keyword.
- Use `lemma connectors operations details linear <OPERATION>` to see the full input schema and required fields.