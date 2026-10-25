/** The parts of a browser sign-in (`handoff.ts`) that are plain functions:
 *  the code both halves show, the link out and the link back. Kept apart
 *  from the native calls so they can be tested on their own. */

/** Letters and digits nobody misreads for one another; the web's alphabet. */
const CODE_ALPHABET = "ACDEFGHJKMNPQRTUVWXY2345679";

/** The code both halves show for one request, from the SHA-256 of
 *  `lemma-desktop-handoff:<request id>`. Must agree with `handoffCode` in
 *  the web's `desktop/auth-handoff.ts`, which the person compares it to. */
export function codeFrom(digest: Uint8Array): string {
    const letters = Array.from(digest.slice(0, 8), (byte) => CODE_ALPHABET[byte % CODE_ALPHABET.length]).join("");
    return letters.slice(0, 4) + "-" + letters.slice(4);
}

export function codeInput(requestId: string): string {
    return "lemma-desktop-handoff:" + requestId;
}

export function base64Url(bytes: Uint8Array): string {
    let binary = "";
    for (const byte of bytes) binary += String.fromCharCode(byte);
    return btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

/** `client=mobile` makes the page name this app rather than Lemma Desktop
 *  (`handoffAppFromSearch` in the web's `desktop/auth-handoff.ts`). */
export function browserUrl(web: string, requestId: string): string {
    return `${web}/auth?desktop_browser=1&desktop_request=${encodeURIComponent(requestId)}&client=mobile`;
}

/** Where the page sends the person once they confirm: this app's scheme. */
export const RETURN_URL = "lemma://auth/complete";

export const REQUEST_ID = /^[A-Za-z0-9_-]{20,128}$/;

/** The request a `lemma://auth/complete?request_id=…` link names, or null
 *  for any other link. */
export function returnedRequest(url: string): string | null {
    const match = /^(?:[a-z][a-z0-9+.-]*:\/\/|\/)auth\/complete\/?\?(.*)$/i.exec(url.trim());
    if (!match) return null;
    const id = new URLSearchParams(match[1]).get("request_id");
    return id && REQUEST_ID.test(id) ? id : null;
}
