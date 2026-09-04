'use client';

import { getLemmaClient } from '@/lib/sdk/lemma-client';
import { forgetBrowserSession } from '@/lib/auth/browser-session';
import { clearLastOpenedPodId } from '@/lib/pods/last-opened-pod';
import { resetAnalyticsIdentity } from '@/lib/analytics/client';

export async function logoutToHome() {
    try {
        // Revoke the SuperTokens session (clears cookies + backend session).
        // There is no upstream/federated SSO logout endpoint to bounce through,
        // so doing the local sign-out and navigating home is the full flow.
        await getLemmaClient().auth.signOut();
    } catch {
        // Best effort: even if the network sign-out fails, fall through and
        // send the user to the landing page rather than stranding them.
    }

    // Falling through is not enough on its own. A sign-out that failed leaves
    // the front token in place, the landing page reads it as a live session,
    // and the user is bounced straight back into the workspace they were trying
    // to leave. Clearing what this browser reads is what makes the navigation
    // below mean something.
    forgetBrowserSession();

    // Drop the "last opened pod" marker so the root route doesn't immediately
    // redirect a just-logged-out user back into their previous pod.
    clearLastOpenedPodId();

    // Before the navigation, and on every sign-out path: without it the next
    // person to use this browser inherits the previous one's analytics identity,
    // and every event they fire is attributed to an account they do not have.
    resetAnalyticsIdentity();

    // Full-document navigation (not router.push) so all in-memory auth/query
    // state is discarded and the landing page renders from a clean slate.
    //
    // The rule disabled here exists to stop a soft navigation being written as
    // a hard one by accident. This one is deliberate and is the whole point of
    // the function: `router.push()` keeps the React Query cache and auth
    // context alive, which is the state a sign-out has to drop.
    // eslint-disable-next-line @next/next/no-location-assign-relative-destination
    window.location.assign('/');
}
