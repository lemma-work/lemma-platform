/** What the person is about to be sent to, said before they go.
 *
 *  A Composio-backed connector signs in on Composio's hosted page, not the
 *  provider's own. Arriving on an unfamiliar domain to approve access to a
 *  mailbox reads as a phishing page unless something said beforehand that
 *  this is where Lemma sends you. So every place that hands over a sign-in
 *  address names the host when it is Composio's, and the app otherwise.
 *
 *  Pure, so the rule can be checked without a browser.
 */

/** Composio's hosted consent pages live under this domain and its subdomains. */
const COMPOSIO_DOMAIN = "composio.dev";

/** Whether `url` is one of Composio's hosted pages. Anything unparseable is
 *  not: the label then falls back to naming the app, which is never wrong. */
export function isComposioUrl(url: string): boolean {
    let host: string;
    try {
        host = new URL(url).hostname.toLowerCase();
    } catch {
        return false;
    }
    return host === COMPOSIO_DOMAIN || host.endsWith("." + COMPOSIO_DOMAIN);
}

/** The words on the link or button that sends the person to `url` to sign
 *  in to `app` — "Continue to Composio to authorize Gmail", or "Continue to
 *  sign in to Gmail". */
export function consentLabel(url: string, app: string): string {
    const what = app.trim() || "the app";
    return isComposioUrl(url) ? "Continue to Composio to authorize " + what : "Continue to sign in to " + what;
}

/** One line under the link, for the Composio case only: why a domain that is
 *  neither Lemma's nor the app's is asking. Empty otherwise. */
export function consentNote(url: string, app: string): string {
    if (!isComposioUrl(url)) return "";
    return "Lemma connects " + (app.trim() || "this app") + " through Composio, so its sign-in page is on composio.dev.";
}
