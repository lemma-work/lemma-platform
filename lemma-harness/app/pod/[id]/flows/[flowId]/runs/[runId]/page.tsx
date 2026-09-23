'use client';

import { use } from 'react';

import { FlowRunPageSurface } from '@/components/flows/flow-execution-panel';

export default function FlowRunPage({
    params,
}: {
    params: Promise<{ id: string; flowId: string; runId: string }>;
}) {
    const { id: podId, flowId, runId } = use(params);

    return (
        <FlowRunPageSurface
            podId={podId}
            flowName={decodeURIComponent(flowId)}
            runId={decodeURIComponent(runId)}
        />
    );
}
