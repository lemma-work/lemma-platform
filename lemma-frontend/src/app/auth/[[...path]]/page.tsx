import { PortalHost } from "../portal-host";

/** The auth portal — every screen, under one route.
 *
 *  Top-level rather than inside `/t`, and that is load-bearing. `SuperTokens
 *  .init` is global and the first call wins; `lemma-sdk` makes one with only
 *  the session recipe, and the listeners that sign somebody out on a 401 are
 *  wired inside it, in module state this app cannot reach. Initialising a
 *  fuller recipe list on a page that also mounted the workspace would not add
 *  to that init — it would replace it, and take the 401 handling with it.
 *  These two pages never meet, so exactly one init runs on either.
 *
 *  The path segments decide the screen (`auth/which.ts`), and every one of
 *  them is a promise made to something outside this app: `reset-password` and
 *  `verify-email` are in emails the backend has already sent, and
 *  `callback/{provider}` is registered with Google and Microsoft.
 */
export default async function AuthRoute({ params }: { params: Promise<{ path?: string[] }> }) {
    const { path } = await params;
    return <PortalHost path={path} />;
}
