'use client';

import { useEffect } from 'react';
import { usePathname } from 'next/navigation';

import { useOrganization } from '@/components/dashboard/org-context';
import { setAnalyticsIdentity } from '@/lib/analytics/client';
import { useLemmaAuth } from '@/lib/hooks/use-lemma-auth';
import { podIdFromPathname } from '@/lib/pods/pod-id-from-pathname';

/**
 * Attaches who this is, and which org and pod they are in, to everything the
 * client sends.
 *
 * Deliberately separate from `AnalyticsProvider`, which mounts app-wide and owns
 * init and pageviews. This one lives *inside* `OrganizationProvider`, because
 * `useOrganization()` throws outside it — and moving the whole of analytics in
 * there would stop capturing the landing and auth pages, which are the top of the
 * funnel this exists to measure.
 *
 * That split costs nothing: identity is meaningless on the auth routes anyway
 * (there is nobody to identify yet), and the anonymous id posthog-js set on the
 * marketing page is the one `identify()` stitches to when this finally runs.
 *
 * Renders nothing.
 */
export function AnalyticsIdentity() {
    const { user } = useLemmaAuth();
    const { currentOrg } = useOrganization();
    const pathname = usePathname();

    const userId = user?.id;
    const organizationId = currentOrg?.id;
    // From the pathname rather than a context: `PodLayoutProvider` mounts far
    // below the app's provider tree and is unreachable from here.
    const podId = podIdFromPathname(pathname);

    useEffect(() => {
        if (!userId) return;
        // One call, so identify-then-group ordering cannot be got wrong, and so
        // leaving a pod re-applies the whole group set rather than leaving the
        // last pod attached to everything that follows.
        setAnalyticsIdentity({ userId, organizationId, podId: podId ?? undefined });
    }, [userId, organizationId, podId]);

    return null;
}
