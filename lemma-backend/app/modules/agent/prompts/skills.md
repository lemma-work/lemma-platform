## Skills

Do not load a skill for ordinary CLI usage, pod file upload/download/search, or LiteParse document parsing — your base guidance already covers them.

Load a skill only for specialized work beyond the above:
- `lemma-builder` — designing, creating, importing/exporting, or editing a pod and its resources (tables, files, functions, agents, workflows, schedules, surfaces, apps, connectors).
- `lemma-user` — broader day-to-day pod operation when the built-in commands above are not enough.
- `lemma-widget` — compact inline conversational views; use a pod app for routing or substantial state.
- `lemma-app-design` — UX, interaction, visual direction, responsive states, accessibility, and design critique for a pod app; pair with `lemma-builder` for implementation.
- `lemma-app-qa` — systematic browser journeys, evidence, defects, and release judgment for a pod app; pair with `browser` for control mechanics.
- `lemma-research` — durable multi-source investigations with source, evidence, claim, freshness, and citation discipline.
- `lemma-data-analysis` — schema-first quantitative analysis, data quality, KPIs, diagnostics, charts, and reproducible outputs.
- `lemma-artifact-author` — create, revise, render, verify, and deliver durable documents, spreadsheets, presentations, PDFs, or HTML.
- `lemma-evals` — repeatable evaluation of agents, functions, and workflows using cases, baselines, rubrics, and regression gates.
- `lemma-skill-creator` — create or revise pod-owned skills under `/skills`; never use it to mutate bundled system skills.
- `browser` — real browser control, screenshots, console/network inspection, forms, scraping, and app interaction.
- `liteparse-documents` — parse or screenshot local documents outside the pod, or fall back when pod conversion is insufficient.
