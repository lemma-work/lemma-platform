'use client';

import { use } from 'react';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { PodSettingsShell } from '@/components/pod/pod-settings-shell';
import { UsageOverview } from '@/components/usage/usage-overview';
import { usePod } from '@/lib/hooks/use-pods';
import { PodSettingsPanelsFill } from '@/components/pod/route-skeletons';

export default function PodUsagePage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <PodUsagePageContent params={params} />
        </ProtectedRoute>
    );
}

function PodUsagePageContent({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);
    const { data: pod, isLoading } = usePod(podId);
    const organizationId = pod?.organization_id;

    return (
        <PodSettingsShell
            podId={podId}
            title="Usage"
        >
            {isLoading ? <PodSettingsPanelsFill panels={3} /> : organizationId ? (
                <UsageOverview
                    organizationId={organizationId}
                    podId={podId}
                    scope="pod"
                    title={`${pod?.name || 'Pod'} usage`}
                />
            ) : (
                <div className="surface-panel p-5 text-sm text-[var(--text-secondary)]">
                    This pod does not include an organization id, so usage cannot be loaded yet.
                </div>
            )}
        </PodSettingsShell>
    );
}
