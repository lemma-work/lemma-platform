"use client";

import { useEffect } from "react";

import { setAnalyticsIdentity, startAnalytics } from "@/site/analytics/client";

/** Who is signed in, and which organization and pod they are working in, for
 *  whichever frame is showing them — the workspace and the outcome frame both,
 *  so neither counts its people as strangers. Null while there is nobody to
 *  name: signed out, still loading, or the sample workspace, whose people are
 *  made up. */
export function useAnalyticsIdentity(
    who: { userId?: string | null; organizationId?: string | null; podId?: string | null } | null,
): void {
    const userId = who?.userId ?? null;
    const organizationId = who?.organizationId ?? undefined;
    const podId = who?.podId ?? undefined;
    useEffect(() => {
        if (!userId) return;
        let current = true;
        void startAnalytics().then(() => {
            if (current) setAnalyticsIdentity({ userId, organizationId, podId });
        });
        return () => { current = false; };
    }, [userId, organizationId, podId]);
}
