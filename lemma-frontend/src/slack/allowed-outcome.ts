/** What the page Slack's Allow lands on says went wrong, if anything.
 *
 *  Read off the callback's fixed `code`, never its `reason`: anybody can send a
 *  link to this page with any `reason` in it, and the page would show it under
 *  Lemma's name. Nothing a link carried is ever shown back. */
export function allowedRefusal(search: string): string | null {
    const query = new URLSearchParams(search);
    if (query.get("connect") !== "error") return null;
    return query.get("code") === "OAUTH_PROVIDER_ERROR"
        ? "Slack didn't allow the app."
        : "Lemma couldn't finish adding the app.";
}
