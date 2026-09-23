/** Which screen a URL under the portal means.
 *
 *  The paths are not this app's to invent. The backend builds the reset and
 *  verification links itself, from `auth_website_base_path` (`/auth`) and
 *  SuperTokens' own conventions, and mails them — so `/auth/reset-password`
 *  and `/auth/verify-email` are a contract with emails already sitting in
 *  people's inboxes. `/auth/callback/{provider}` is the same kind of promise
 *  made to Google and Microsoft, registered with them as a redirect URI.
 *
 *  Which is why this is a table rather than a router: every row is a string
 *  somebody else is already holding.
 */

export type Screen = "sign-in" | "sign-up" | "reset" | "verify" | "callback" | "unknown";

export function screenFor(path: string[] | undefined): Screen {
    const segments = (path ?? []).filter(Boolean);
    if (segments.length === 0) return "sign-in";

    switch (segments[0]) {
        case "signin":
        case "login":
            /* Not spellings this app links to, but ones people type and older
               links carry. They cost a line each and save a dead end. */
            return segments.length === 1 ? "sign-in" : "unknown";
        case "signup":
            return segments.length === 1 ? "sign-up" : "unknown";
        case "reset-password":
            return segments.length === 1 ? "reset" : "unknown";
        case "verify-email":
            return segments.length === 1 ? "verify" : "unknown";
        case "callback":
            /* The provider id is in the path but is not read here: SuperTokens
               recovers which provider this was from the state it stored before
               leaving, and trusting the URL instead would mean believing
               whoever sent the browser back about who they are. */
            return segments.length === 2 ? "callback" : "unknown";
        default:
            return "unknown";
    }
}
