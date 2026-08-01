'use client';

import { use } from 'react';

import { ConceptHint } from '@/components/education/concept-hint';
import { ConnectorsView } from '@/components/connectors/connectors-view';
import { ResourceHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
import { usePod } from '@/lib/hooks/use-pods';
import { StepLoader } from '@/components/brand/loader';

export default function PodConnectorsPage({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);
    const { data: pod, isLoading } = usePod(podId);

    if (isLoading) {
        return (
            <div className="flex h-full items-center justify-center">
                <StepLoader size="sm" />
            </div>
        );
    }

    return (
        <ResourceIndexShell>
            <ResourceHeader
                title="Connectors"
                meta={<ConceptHint concept="connector" />}
            />
            <ConnectorsView
                embedded
                showHeader={false}
                organizationId={pod?.organization_id}
            />
        </ResourceIndexShell>
    );
}
