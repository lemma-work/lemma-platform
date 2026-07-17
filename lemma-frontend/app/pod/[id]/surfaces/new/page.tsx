'use client';

import { use } from 'react';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { PodSurfacesPanel } from '@/components/pod/pod-channels-panel';
import { PodPageHeader } from '@/components/pod/pod-page-header';

export default function NewSurfacePage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);

    return (
        <ProtectedRoute>
            <div className="agent-builder-root flex h-full min-h-0 flex-col">
                <PodPageHeader
                    podId={podId}
                    variant="bar"
                    title="Configure surfaces"
                    eyebrow="External entry points"
                    backHref={`/pod/${podId}/surfaces`}
                    backLabel="Surfaces"
                    productIconKind="surfaces"
                />
                <main className="min-h-0 flex-1 overflow-y-auto">
                    <div className="agent-builder-canvas mx-auto w-full max-w-[76rem] px-6 pb-24 pt-6">
                        <PodSurfacesPanel podId={podId} mode="create" />
                    </div>
                </main>
            </div>
        </ProtectedRoute>
    );
}
