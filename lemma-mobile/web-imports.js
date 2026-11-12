/* The web's own code, run where it is (`lemma-frontend/src`) instead of
   copied into the app. App code imports it as `@web/<path>`; inside the
   web's files `@/` means that same folder, as it does on the web. A package
   a web file imports comes from the app's node_modules, never the web's:
   one React, one React Query, one SDK.

   Metro (`metro.config.js`), vitest (`vitest.config.ts`) and TypeScript
   (`tsconfig.json` `paths`, which cannot read this file) all resolve this
   way; `tests/web-imports.test.ts` holds tsconfig to that, holds each shim
   to its web module's names, and fails on a file in `src/` that is a copy
   of a web module. The WebView pages
   (`webview/build.mjs`) do the same with esbuild. */

const path = require("path");

const WEB = path.resolve(__dirname, "../lemma-frontend/src");

/** Web modules that cannot run on a phone, each answered by the app's own:
 *  the web's file (under `WEB`) → the app's. Swapped however a web file
 *  reaches it, `@/session/storage` or `./storage`. TypeScript still checks
 *  the web's own file, so a shim exports the same names with the same types. */
const SHIMS = {
    "session/storage.ts": "src/web-shims/session-storage.ts",
};

module.exports = { WEB, SHIMS };
