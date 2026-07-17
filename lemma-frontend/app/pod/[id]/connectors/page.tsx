'use client';

import { use } from 'react';

import { SectionPrimer } from '@/components/education/section-primer';
import { ConnectorsView } from '@/components/connectors/connectors-view';
import { ResourceIndexHeader, ResourceIndexShell } from '@/components/pod/resource-layout';
import { StepLoader } from '@/components/brand/loader';
import { usePod } from '@/lib/hooks/use-pods';

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
            <ResourceIndexHeader
                title="Connectors"
                productIconKind="connectors"
                meta={<span>{pod?.name || 'Pod'}</span>}
            />
            <SectionPrimer concept="connector" className="mb-4" />
            <ConnectorsView
                embedded
                showHeader={false}
                organizationId={pod?.organization_id}
            />
        </ResourceIndexShell>
    );
}
