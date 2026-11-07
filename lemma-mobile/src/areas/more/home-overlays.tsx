import { useState } from "react";
import { installArrival } from "./install/arrival";
import { TourHost } from "./tour/host";
import { FirstProfileStep } from "./welcome/first-profile-step";

/** What opens over Home by itself, once: the first profile step, then the
 *  tour. One after the other and never together, and neither on the visit
 *  an install opens (as on the web). Mounted once on Home. */
export function HomeOverlays({ org, ready }: { org: string | null; ready: boolean }) {
    const [profileShown, setProfileShown] = useState<boolean | null>(null);
    return (
        <>
            <FirstProfileStep hold={installArrival.held()} onShown={setProfileShown} />
            <TourHost org={org} ready={ready && profileShown === false} />
        </>
    );
}
