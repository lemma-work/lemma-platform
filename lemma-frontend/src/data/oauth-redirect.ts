import { useConnector } from "@/connect/queries";

/** The redirect URI an OAuth app registered for this deployment must allow.
 *
 *  Read from the backend, never assembled here. It is built by the backend's
 *  `OAuthRedirectUriBuilder` and published on the connector detail
 *  (`oauth_redirect_uri`); a copy built in this app had a path the backend
 *  does not serve, and every sign-in through an app registered with it failed
 *  with `redirect_uri_mismatch`. */
export function redirectUriOf(detail: unknown): string | null {
    const uri = (detail as { oauth_redirect_uri?: unknown } | null | undefined)?.oauth_redirect_uri;
    return typeof uri === "string" && uri.trim() ? uri.trim() : null;
}

/** The same, as a hook. The URI is the deployment's, so any catalogued
 *  connector answers it; pass the one on screen to share its cached detail.
 *  The default is a surface connector, which every deployment catalogues.
 *  Null while loading, off a live workspace, or from an older server. */
export function useOAuthRedirectUri(connectorId: string = "slack"): string | null {
    return redirectUriOf(useConnector(connectorId).data);
}
