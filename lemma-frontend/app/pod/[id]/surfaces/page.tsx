'use client';

import { use } from 'react';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { ConceptHint } from '@/components/education/concept-hint';
import { SectionPrimer } from '@/components/education/section-primer';
import { PodSurfacesPanel } from '@/components/pod/pod-channels-panel';
import { ResourceIndexHeader, ResourceIndexShell } from '@/components/pod/resource-layout';

export default function PodSurfacesPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id: podId } = use(params);

    return (
        <ProtectedRoute>
            <ResourceIndexShell>
                <ResourceIndexHeader
                    title="Surfaces"
                    productIconKind="surfaces"
                    meta={<ConceptHint concept="surface" />}
                />
                <SectionPrimer concept="surface" className="mb-4" />
                <PodSurfacesPanel podId={podId} showHeading={false} />
            </ResourceIndexShell>
        </ProtectedRoute>
    );
}
