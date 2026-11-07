# From the web

The files area runs the web's own code in two ways.

**Pure modules, imported where they live** (`@web/docpages/*`,
`@web/library/*`, `@web/thread/document-save`, `@web/thread/resource-conversation`,
`@web/skills/skill-frontmatter`; see `../../../../web-imports.js`). `types.ts`
holds the few shapes the phone's own code reads, and `page-chat.ts` is
`instructionsFor` from `chat/floating-chat.tsx`, a component file, held to the
web by `tests/web-copies.test.ts`.

**The page editor and the PDF reader, built.** `editor-page.generated.ts` is
the web's `thread/document-editor.tsx` with its extensions, slash menu,
selection toolbar, live table views, widgets and images, bundled from the web's
sources into one HTML page by `lemma-mobile/webview` and run in a WebView
(`../editor.tsx`). `pdf-page.generated.ts` is pdf.js. Each records a hash of
what it was built from, and the same test fails when a source changed since:

```bash
cd lemma-mobile/webview && npm ci && npm run build
```

`protocol.ts` is everything the app and the editor page say to each other.
The page asks the app for every read and write; it never holds a token.
