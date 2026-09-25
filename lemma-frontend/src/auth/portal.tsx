"use client";

import { PageLoading } from "@/ui/loading";

import { useEffect, useState } from "react";
import { Session, startAuth } from "./supertokens";
import { screenFor } from "./which";
import { Callback, Reset, SignInUp } from "./screens";
import { Verify } from "./verification-screen";
import { PORTAL_PATH, asksForSignUp } from "./config";
import { hasApiUrl } from "@/session/client";
import { holdRequestId, requestIdFromSearch, shouldUseBrowserHandoff } from "@/desktop/auth-handoff";
import { DesktopReturn, DesktopSignIn } from "@/desktop/sign-in";

/** The portal, mounted.
 *
 *  Browser-only, and deliberately so: `SuperTokens.init` reads and writes
 *  browser storage and would have nothing to say on a server. The route beside
 *  this renders the document and this takes over after hydration, which is the
 *  same split `/t` makes for the workspace.
 *
 *  `startAuth` runs before any screen does. A recipe function called against
 *  an uninitialised SuperTokens throws something about initialisation rather
 *  than anything a person could act on.
 */
export function Portal({ path }: { path?: string[] }) {
    const [ready, setReady] = useState(false);

    useEffect(() => {
        startAuth();
        /* A browser the desktop app sent here to sign in. The request is held
           across the provider round trip, and a browser that is already signed
           in goes straight to handing the session back. */
        const desktopRequest = requestIdFromSearch(window.location.search);
        if (desktopRequest) {
            holdRequestId(desktopRequest);
            void Session.doesSessionExist().then((signedIn) => {
                if (signedIn) window.location.replace(PORTAL_PATH + "/desktop");
            });
        }
        setReady(true);
    }, []);

    if (!hasApiUrl()) {
        /* Nothing to sign in against. The one screen that must never be blank
           is the one somebody reaches when the app is misconfigured. */
        return (
            <div className="screen"><div className="screen__inner auth">
                <h2>This one is not set up yet</h2>
                <p>
                    There is no API address here, so there is nothing to sign in to. Whoever
                    deployed this needs to give it one.
                </p>
                <p className="screen__footnote"><code>NEXT_PUBLIC_API_URL</code></p>
            </div></div>
        );
    }

    if (!ready) return <PageLoading label="Opening sign in" />;

    /* The desktop app on a hosted workspace signs in through the system
       browser rather than in its own webview. */
    const handoff = shouldUseBrowserHandoff();
    const screen = screenFor(path, window.location.search);
    /* The bare door can also be asked for sign-up in the hash; any deeper
       path already says which screen it is and keeps it. */
    const signUp = screen === "sign-in" && (path ?? []).length === 0
        && asksForSignUp(window.location.search, window.location.hash);
    switch (signUp ? "sign-up" : screen) {
        case "sign-in": return handoff ? <DesktopSignIn mode="in" /> : <SignInUp mode="in" />;
        case "sign-up": return handoff ? <DesktopSignIn mode="up" /> : <SignInUp mode="up" />;
        case "desktop": return <DesktopReturn />;
        case "reset": return <Reset />;
        case "verify": return <Verify />;
        case "callback": return <Callback />;
        default:
            return (
                <div className="screen"><div className="screen__inner auth">
                    <h2>Nothing lives at this address</h2>
                    <p>That link does not name a page we have. Check it, or start from the front.</p>
                    <div className="screen__actions">
                        <a className="btn btn--primary" href={PORTAL_PATH}>Go to sign in</a>
                    </div>
                </div></div>
            );
    }
}
