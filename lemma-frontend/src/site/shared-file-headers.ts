/** The headers a shared file is served back with through `/d/<code>/file`.
 *
 *  The API already decides how its bytes may be rendered: `/s/<code>` answers
 *  `inline` only for types a browser renders without running anything (PDF,
 *  images other than SVG, audio, video, plain text) and `attachment` for the
 *  rest, with `nosniff`. This proxy copied the type and dropped every one of
 *  those — so an uploaded `.html` or `.svg`, shared by link, rendered here as a
 *  document on this app's origin, where the session lives. A stored XSS for
 *  anybody who could share a file.
 *
 *  So: the API's disposition travels through, an attachment it asked for is
 *  never loosened, a type that is not inline-safe is an attachment even if the
 *  API said nothing, `nosniff` stops a browser guessing its way back to HTML,
 *  and `sandbox` makes whatever does render a unique opaque origin with no
 *  script. Pure, so the rule is tested rather than trusted.
 */

const INLINE_PREFIXES = ["application/pdf", "image/", "audio/", "video/"];
const INLINE_EXACT = ["text/plain"];
const NEVER_INLINE = ["image/svg+xml"];

/** The same allowlist as the API's `is_inline_media_type`. */
export function isInlineSafe(contentType: string): boolean {
    const base = contentType.split(";", 1)[0].trim().toLowerCase();
    if (NEVER_INLINE.includes(base)) return false;
    return INLINE_EXACT.includes(base) || INLINE_PREFIXES.some((prefix) => base.startsWith(prefix));
}

function asAttachment(disposition: string | null): string {
    if (!disposition) return "attachment";
    return /^\s*inline\b/i.test(disposition) ? disposition.replace(/^\s*inline/i, "attachment") : disposition;
}

export function sharedFileHeaders(upstream: Headers, save: boolean): Headers {
    const headers = new Headers();
    const type = upstream.get("content-type") ?? "application/octet-stream";
    headers.set("content-type", type);
    const length = upstream.get("content-length");
    if (length) headers.set("content-length", length);
    /* The reader's browser may cache it; nothing shared may. A capability
       code is the whole of the permission, so a shared cache holding these
       bytes would be handing them to whoever asked next. */
    headers.set("cache-control", "private, max-age=60");

    const disposition = upstream.get("content-disposition");
    const inline = !save && isInlineSafe(type) && !/^\s*attachment\b/i.test(disposition ?? "");
    headers.set("content-disposition", inline ? disposition ?? "inline" : asAttachment(disposition));
    headers.set("x-content-type-options", "nosniff");
    /* Not on a PDF shown in place: Chrome will not run its viewer inside a
       sandboxed document, and a PDF is one of the types that is inline because
       it cannot script this origin. */
    if (!(inline && type.toLowerCase().startsWith("application/pdf"))) {
        headers.set("content-security-policy", "sandbox; default-src 'none'; img-src 'self' data:; media-src 'self'; style-src 'unsafe-inline'");
    }
    return headers;
}
