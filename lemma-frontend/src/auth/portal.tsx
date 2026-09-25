"use client";

import { PageLoading } from "@/ui/loading";

import { useEffect, useState } from "react";
import { startAuth } from "./supertokens";
import { screenFor } from "./which";
import { Callback, Reset, SignInUp } from "./screens";
import { Verify } from "./verification-screen";
import { PORTAL_PATH } from "./config";
import { hasApiUrl } from "@/session/client";

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

    switch (screenFor(path, window.location.search)) {
        case "sign-in": return <SignInUp mode="in" />;
        case "sign-up": return <SignInUp mode="up" />;
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
