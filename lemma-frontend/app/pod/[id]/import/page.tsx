'use client';

import { use } from 'react';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { ImportPodBundleWizard } from '@/components/pod/import-pod-bundle-wizard';
import { PodSettingsShell } from '@/components/pod/pod-settings-shell';
import { SharePodSheet } from '@/components/pod/share-pod-sheet';

export default function PodImportPage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <PodImportPageContent params={params} />
        </ProtectedRoute>
    );
}

function PodImportPageContent({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);

    return (
        <PodSettingsShell
            podId={podId}
            title="Import / export"
            description="Share this pod or bring one in — reviewing what it does and needs before applying."
            action={<SharePodSheet podId={podId} />}
        >
            <ImportPodBundleWizard podId={podId} />
        </PodSettingsShell>
    );
}
