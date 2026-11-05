/** What the signed-in person may do in an organization, as permission ids.
 *
 *  Pages ask for a permission — `org.installation.manage` — never for a role,
 *  so the day a role's holdings change the pages need not. The list is the
 *  server's own answer (`GET /organizations/{org}/permissions/me`), asked of
 *  the same authorizer that decides the request a page would then make. This
 *  file used to turn my role into permissions with a copy of the server's
 *  `SYSTEM_ROLE_PERMISSIONS`; a copy is a second answer, and two answers drift.
 */

export const INSTALLATION_MANAGE = "org.installation.manage";

/** The permission ids off the server's answer. Anything unreadable holds
 *  nothing: a page withholds what it cannot tell is allowed. */
export function readOrgActions(raw: unknown): string[] {
    const actions = (raw as { actions?: unknown } | null)?.actions;
    return Array.isArray(actions) ? actions.filter((one): one is string => typeof one === "string") : [];
}
