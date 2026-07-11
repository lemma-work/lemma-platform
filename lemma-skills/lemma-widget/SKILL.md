---
name: lemma-widget
description: "Create lightweight inline Lemma widgets for conversations via display_resource(type=\"WIDGET\"): self-contained HTML/CSS/JS or SVG for metrics, lists, comparisons, timelines, record details, previews, and charts, optionally powered by live pod data through the browser Lemma SDK. Use an app, not a widget, when the UI needs React, routing, or substantial application state."
---

# Lemma Widget

A widget is the default way to **show an answer that is more than short prose**.
Use `display_resource(type="WIDGET")` whenever the useful result has structure or
visual hierarchy: several values, records, statuses, steps, comparisons, a timeline,
a compact table, a preview, or a chart.

Use plain text only for a single fact, a short explanation, or narration around the
widget. If an existing FILE, TABLE, APP, or other pod resource already represents the
answer, display that resource directly instead of recreating it as a widget.

## Widget or app?

- **Widget:** one compact inline view; plain HTML/CSS/JS or SVG; quick to render in
  the conversation; little local state.
- **Vite app:** React, routing, multiple screens, reusable components, substantial
  interaction/state, or a UI people will return to as a product.

Do not load React, ReactDOM, Tailwind, or the agent web-component bundle inside a
widget. Lemma already has full Vite app support for that class of UI. A widget may be
saved as an HTML app later, but it should remain lightweight.

Widgets are display surfaces. They do not submit values into the conversation. Use
`ask_user` for fixed choices or ask for free-form input in prose.

## Build one

1. If the widget uses pod data, inspect the real table and column names first:

   ```bash
   lemma pods describe
   lemma tables list
   lemma query run "select * from <table> limit 5"
   ```

2. Load the closest maintained starter with `load_skill`, using
   `name="lemma-widget"` and one of these `resource_path` values:

   | Answer shape | Starter |
   | --- | --- |
   | Metrics grouped by one field | `assets/widget-starter-v1.html` |
   | Compact record list | `assets/widget-list-v1.html` |
   | Bar chart grouped by one field | `assets/widget-chart-v1.html` |
   | One record with selected fields | `assets/widget-detail-v1.html` |

3. Replace every uppercase `__PLACEHOLDER__` with inspected names and useful labels.
   For `__FIELD_CONFIG__`, insert a JSON array such as
   `[{"label":"Owner","field":"owner"}]`.

4. Adapt the content and styling, then call `display_resource` with `type="WIDGET"`.
   Do not rebuild the SDK loader or loading/empty/error scaffolding from scratch.

The backend rejects unresolved placeholders and broken SDK loaders before display.

## Fixed contract

- Send an HTML/SVG **fragment**, never `<!doctype>`, `<html>`, `<head>`, or `<body>`.
- Keep all CSS local. The widget runs in its own iframe and inherits no frontend CSS.
- Use plain browser JavaScript. No build step, JSX, React, or framework runtime.
- Never put secrets, credentials, a pod id, or an environment hostname in the HTML.
- Show deliberate loading, empty, error, and narrow-screen states.
- Escape values before inserting them with `innerHTML`; prefer `textContent`.
- Keep the view compact: no fixed positioning or nested scrolling.

The starters are platform-themed and system-aware by default. Preserve their
`prefers-color-scheme: dark` rules and semantic fallbacks. They consume the small
public token layer `--lemma-widget-bg`, `surface`, `subtle`, `text`, `muted`,
`border`, `accent`, `danger`, `radius`, `font`, and `color-scheme` (each with the full
`--lemma-widget-` prefix). Chart starters also expose `chart-1` through `chart-5`.
Use only the tokens the widget needs; do not copy Lemma's full frontend token file.
Never reference frontend-only variables such as `--text-primary` directly.

For a data-backed widget, preserve the starter's browser SDK loader:

- Build the SDK URL from `window.__LEMMA_CONFIG__.apiUrl`.
- Load `/public/sdk/lemma-client.js` dynamically and start in `sdk.onload`.
- Construct `new window.LemmaClient.LemmaClient()` with no arguments.
- Call `client.initialize()` and handle a non-authenticated state.
- SDK calls run as the signed-in user under normal RLS and grants.
- Shared files use `/…`; personal files use `/me`; never use `/pod/...`.

Common calls:

```js
await client.records.list("tickets", { limit: 50 });
await client.records.get("tickets", "record-id");
await client.datastore.query(
  "select status, count(*) as total from tickets group by status"
);
await client.files.search("quarterly planning", { limit: 10 });
await client.files.children.markdown("/knowledge/report.pdf");
await client.files.children.content(
  "/knowledge/report.pdf/pages/page_0001.jpg"
);
```

Prefer `datastore.query` for aggregates. Never poll with `setInterval`; if a widget
must stay live, use `client.datastore.watchChanges`.

## Visual standard

- Lead with the answer, not a title-heavy dashboard shell.
- Follow Lemma's neutral surfaces, near-black/near-white text, indigo action color,
  restrained borders, and compact radii unless the content needs a distinct visual
  language.
- For a very small widget, the token-light set is just `surface`, `text`, `muted`,
  `border`, and `accent`.
- Format numbers and dates for humans.
- Use a list for repeated records, cards for a handful of metrics, a detail layout
  for one record, and a chart only when shape or comparison matters.
- For Chart.js, give the canvas wrapper an explicit height and read chart/text/grid
  colors from the starter's semantic variables.
- Keep explanation outside the widget in the assistant response.

## Before display

- The chosen view is genuinely more useful than short prose.
- The closest versioned starter was used and all placeholders were replaced.
- The fragment contains no full-document tags, secrets, hardcoded hosts, or pod ids.
- SDK code uses injected config and boots from the script load handler.
- Loading, empty, error, and mobile states are present.

For React or a full product UI, load `lemma-builder` and follow
`references/apps.md`. For interaction-tool behavior, see
`lemma-builder/references/agent-tools.md`.
