"use client";

import { useEffect } from "react";
import { useRouter } from "next/navigation";
import { signedIn } from "@/session/who";

/** Signed in? Then this is not the page you wanted.
 *
 *  The root route is the marketing page, and it is server-rendered as such,
 *  because the visitor a cold first paint is for is a logged-out one. Who is
 *  asking cannot be known any earlier than this: every fact the session is
 *  decided from — the SDK's auth manager, the chosen API origin, the bearer
 *  token on localhost — lives in browser storage, which a server reads none
 *  of. There is no cookie on this origin to check and no honest server-side
 *  answer to give, so the page renders for the common case and steps aside
 *  once the browser knows better.
 *
 *  `replace`, not `push`: this was never a place in the history, and pushing
 *  it would make Back bounce off the redirect and strand somebody on it.
 *
 *  Only a settled yes leaves, and settling it means ending at `GET /users/me`
 *  rather than at the SDK's answer — see `signedIn`. Nothing is shown while it
 *  settles: a spinner thrown over a marketing page for the length of one
 *  request is worse than the page. Unconfigured stays, because with no API
 *  origin there is nothing to ask and the setup screen is a worse answer than
 *  the page explaining what this is; sample stays, having no session to be in.
 */
export function ToTheApp() {
    const router = useRouter();

    useEffect(() => {
        let live = true;
        /* One settled answer, from the same module the workspace's own gate
           uses. The first pass asked the SDK and believed it, which is the one
           thing that cannot be done here: arriving from the auth site there is
           no front token on this origin, so `doesSessionExist()` is false, the
           SDK answers `unauthenticated` without a request, and the front door
           showed the marketing page to somebody already signed in. */
        void signedIn().then(yes => { if (live && yes) router.replace("/t"); });
        return () => { live = false; };
    }, [router]);

    return null;
}
