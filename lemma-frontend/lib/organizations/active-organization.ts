/**
 * The organization a route belongs to, if it names one.
 *
 * Shared because two things need to agree on it: the settings sidebar, which
 * highlights that organization, and `OrganizationProvider`, which decides which
 * organization the rest of the app acts on. The provider used to seed itself
 * only from localStorage, so following a direct link to one organization's
 * settings left "current" pointing at whichever one was last used -- and
 * creating a pod from there filed it under the wrong organization.
 */
export function activeOrganizationIdFrom(
    pathname: string | null | undefined,
): string | undefined {
    return pathname?.match(/^\/organizations\/([^/]+)(?:\/|$)/)?.[1];
}
