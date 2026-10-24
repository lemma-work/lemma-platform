/** The redirect URI an OAuth app registered for this deployment must allow
 *  (the web's `data/oauth-redirect.ts` `redirectUriOf`; its other half is a
 *  hook over the web's own query, so the pure half is said here).
 *
 *  Read from the server, never assembled here. The backend builds it and
 *  publishes it on the connector detail (`oauth_redirect_uri`); a copy built
 *  in an app had a path the backend does not serve, and every sign-in through
 *  an app registered with it failed with `redirect_uri_mismatch`. */
export function redirectUriOf(detail: unknown): string | null {
    const uri = (detail as { oauth_redirect_uri?: unknown } | null | undefined)?.oauth_redirect_uri;
    return typeof uri === "string" && uri.trim() ? uri.trim() : null;
}
