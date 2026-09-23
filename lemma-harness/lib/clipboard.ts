/**
 * Copying text from a page that may not be a secure context.
 *
 * `navigator.clipboard` does not exist outside a secure context, and the
 * desktop workspace is served from an `http://*.localhost` origin that WKWebView
 * does not treat as trustworthy. So the property is `undefined` there and
 * `navigator.clipboard.writeText(...)` throws a `TypeError` before any promise
 * exists — which is not the "permission denied" every call site was written to
 * expect. The message copy button caught it and did nothing at all: no text
 * copied, no error, no feedback.
 *
 * `document.execCommand('copy')` is deprecated and still the only thing that
 * works there, so it is the fallback rather than the first choice.
 *
 * Throws when neither path worked, so it drops into the `try`/`catch` every
 * call site already had around `writeText` and their existing "could not copy"
 * toast keeps meaning what it says.
 */
export async function copyText(text: string): Promise<void> {
    // Optional-chained, because the failure to guard against is the property
    // being absent, not the promise rejecting.
    if (typeof navigator !== 'undefined' && navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return;
        } catch {
            // Fall through: a denied permission in one context is not proof the
            // older path is denied too.
        }
    }
    if (!copyWithExecCommand(text)) {
        throw new Error('the clipboard is not available in this context');
    }
}

/** The pre-async-clipboard path, which a non-secure context still allows. */
function copyWithExecCommand(text: string): boolean {
    if (typeof document === 'undefined') return false;
    const area = document.createElement('textarea');
    area.value = text;
    // Off-screen rather than hidden: `execCommand` copies the *selection*, and
    // a `display: none` element cannot hold one.
    area.setAttribute('readonly', '');
    area.style.position = 'fixed';
    area.style.top = '-9999px';
    area.style.opacity = '0';
    document.body.appendChild(area);
    try {
        area.select();
        area.setSelectionRange(0, text.length);
        return document.execCommand('copy');
    } catch {
        return false;
    } finally {
        area.remove();
    }
}
