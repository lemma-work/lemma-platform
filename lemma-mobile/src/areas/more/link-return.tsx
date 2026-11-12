import { useEffect } from "react";
import { usePathname, useRouter } from "expo-router";
import { linkArrived, takeWaitingLink } from "./links";

/** A link that arrived while signed out, opened once somebody is in.
 *
 *  Mounted with the signed-in screens (`(in)/_layout.tsx`): every screen
 *  that opens is reported, so a link whose screen the router opened itself
 *  is crossed off, and whatever is still waiting a moment after signing in
 *  (an invitation from an email, say) is opened then. */
export function LinkReturn() {
    const router = useRouter();
    const pathname = usePathname();
    useEffect(() => {
        linkArrived(pathname);
    }, [pathname]);
    useEffect(() => {
        const timer = setTimeout(() => {
            const path = takeWaitingLink();
            if (path) router.push(path as never);
        }, 900);
        return () => clearTimeout(timer);
    }, [router]);
    return null;
}
