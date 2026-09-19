'use client';

import { use } from 'react';

import { ConceptHint } from '@/components/education/concept-hint';
import { ConnectorsView } from '@/components/connectors/connectors-view';
import { BrowserLoginsCard } from '@/components/web-logins/browser-logins-card';
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
            {/*
              The sandbox browser's logins are the same idea as a connector
              account — my credential at a third party — reached a different
              way, so they live on this page rather than becoming a second
              place to look, and in "Add your own" rather than stapled
              underneath: a section of its own below eighty app cards read as
              a footnote to the page instead of part of it.
            */}
            <ConnectorsView
                embedded
                showHeader={false}
                organizationId={pod?.organization_id}
                extraOwnConnection={<BrowserLoginsCard />}
            />
        </ResourceIndexShell>
    );
}
