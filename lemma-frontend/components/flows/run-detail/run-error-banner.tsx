'use client';

// Why the run stopped, said once, at the top.
//
// The engine funnels every failure through `run.fail()`, which records the
// reason and the node that raised it, and the API has always shipped both. The
// run UI parsed them and rendered neither — so a failed run was a red dot, the
// word "Failed", and a step panel that was empty precisely because the message
// went to `step.error` while the panel only read `step.output_data`.

import { AlertTriangle } from '@/components/ui/icons';
import { cn } from '@/lib/utils';
import { JsonView } from '@/components/shared/json-view';
import { getDisplayNodeLabel } from '../run-format';
import type { WorkflowNode } from '@/lib/types';

export function RunErrorBanner({
    error,
    failedNodeId,
    nodes,
    className,
}: {
    error?: string | null;
    failedNodeId?: string | null;
    nodes: WorkflowNode[];
    className?: string;
}) {
    const message = typeof error === 'string' ? error.trim() : '';
    if (!message) return null;

    const failedNode = failedNodeId ? nodes.find((node) => node.id === failedNodeId) || null : null;
    const failedLabel = failedNode ? getDisplayNodeLabel(failedNode) : failedNodeId || null;

    return (
        <div className={cn('state-surface-error flex items-start gap-3 rounded-lg px-4 py-3', className)} role="alert">
            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
            <div className="min-w-0 flex-1">
                <p className="text-sm font-semibold">
                    {failedLabel ? `Failed at ${failedLabel}` : 'Run failed'}
                </p>
                {/* Reasons are bounded to 2000 chars server-side and are usually a
                    single line, but a chained exception can carry structure — so
                    let JsonView decide between prose and a block. */}
                <JsonView value={message} density="compact" className="mt-1 text-sm" />
            </div>
        </div>
    );
}
