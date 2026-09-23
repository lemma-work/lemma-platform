/**
 * Types for `@novnc/novnc`'s default export.
 *
 * `@types/novnc__novnc` declares its module at `@novnc/novnc/lib/rfb`, which
 * is where noVNC's *older*, Babel-compiled build lived. 1.7.0 no longer ships
 * that build at all -- `lib/rfb.js` mixed CommonJS `exports.x = ...`
 * assignments with a genuine top-level `await` in one of its own dependencies
 * (`util/browser.js`'s WebCodecs feature probe), which is invalid CommonJS
 * and broke under bundling: `browser.isMac` came back `undefined` at runtime
 * no matter how the import was written. 1.7.0 ships its real ES module
 * source instead, resolved through the package's own `"exports": "./core/rfb.js"`
 * -- reachable only as the bare specifier `@novnc/novnc`, since a string
 * `"exports"` value is the whole of the package's public map and admits no
 * deep import. This file only re-points the existing, otherwise-identical
 * type declarations at that specifier.
 */
declare module "@novnc/novnc" {
    export { default } from "@novnc/novnc/lib/rfb";
}
