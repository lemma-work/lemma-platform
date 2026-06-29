'use client';

import { use } from 'react';
import { Loader2 } from 'lucide-react';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { PodSettingsShell } from '@/components/pod/pod-settings-shell';
import { UsageOverview } from '@/components/usage/usage-overview';
import { usePod } from '@/lib/hooks/use-pods';

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

    if (isLoading) {
        return (
            <div className="context-shell flex min-h-full items-center justify-center bg-transparent">
                <div className="surface-panel px-5 py-4">
                    <Loader2 className="h-5 w-5 animate-spin text-[var(--text-tertiary)]" />
                </div>
            </div>
        );
    }

    return (
        <PodSettingsShell
            podId={podId}
            title="Pod Settings"
            description="Track spend, tokens, and recent model activity inside this pod."
        >
            <div className="w-full max-w-5xl">
                {organizationId ? (
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
            </div>
        </PodSettingsShell>
    );
}
