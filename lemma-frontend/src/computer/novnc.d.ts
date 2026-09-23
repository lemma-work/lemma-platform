/** Types for `@novnc/novnc`'s default export.
 *
 *  `@types/novnc__novnc` declares its module at `@novnc/novnc/lib/rfb`, which
 *  is where noVNC's older Babel-compiled build lived. 1.7.0 does not ship that
 *  build at all: it ships its ES module source, published through the
 *  package's own `"exports": "./core/rfb.js"` — a string `exports` value is the
 *  whole of a package's public map and admits no deep import, so the bare
 *  specifier is the only way in. This re-points the otherwise identical
 *  declarations at the specifier that exists.
 */
declare module "@novnc/novnc" {
    export { default } from "@novnc/novnc/lib/rfb";
}
