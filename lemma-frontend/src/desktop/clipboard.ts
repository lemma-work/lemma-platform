/** Copying text from a page that may not be a secure context.
 *
 *  `navigator.clipboard` exists only in a secure context. The local desktop
 *  workspace on `http://app.lemma.localhost` is one -- every engine treats
 *  `*.localhost` as potentially trustworthy -- but the same workspace shared on
 *  the LAN is plain `http://192.168.x.y`, which is not. There the property is
 *  `undefined`, and `navigator.clipboard.writeText(...)` throws a `TypeError`
 *  before any promise exists — not the "permission denied" every call site was
 *  written to expect.
 *  A copy button that caught it did nothing at all: no text, no error, no
 *  feedback.
 *
 *  `document.execCommand("copy")` is deprecated and still the only thing that
 *  works there, so it is the fallback rather than the first choice.
 *
 *  Rejects when neither path worked, so every call site keeps its own "could
 *  not copy" answer meaning what it says. Use this, never `navigator.clipboard`
 *  directly.
 */
export async function copyText(text: string): Promise<void> {
    /* Optional-chained: the failure to guard against is the property being
       absent, not the promise rejecting. */
    if (typeof navigator !== "undefined" && navigator.clipboard?.writeText) {
        try {
            await navigator.clipboard.writeText(text);
            return;
        } catch {
            /* A denied permission here is not proof the older path is denied
               too. */
        }
    }
    if (!copyWithExecCommand(text)) {
        throw new Error("The clipboard is not available here.");
    }
}

/** The pre-async-clipboard path, which a non-secure context still allows. */
function copyWithExecCommand(text: string): boolean {
    if (typeof document === "undefined") return false;
    const area = document.createElement("textarea");
    area.value = text;
    /* Off-screen rather than hidden: `execCommand` copies the selection, and a
       `display: none` element cannot hold one. */
    area.setAttribute("readonly", "");
    area.style.position = "fixed";
    area.style.top = "-9999px";
    area.style.opacity = "0";
    document.body.appendChild(area);
    try {
        area.select();
        area.setSelectionRange(0, text.length);
        return document.execCommand("copy");
    } catch {
        return false;
    } finally {
        area.remove();
    }
}
