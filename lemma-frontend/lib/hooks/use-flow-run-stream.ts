'use client';

// Live run state over SSE, with polling left in place underneath.
//
// A run was the only live thing here that did not stream, so every viewer paid
// a GET every two seconds and still saw state up to two seconds stale. The
// server sends the whole run per frame rather than a diff, which makes this a
// replace-into-cache reducer: no merge logic, and a reconnect is just the next
// opening frame.
//
// This deliberately does not disable polling. The stream is a latency
// improvement, not a correctness dependency — if it never connects, or drops
// and cannot be re-established, the existing poll keeps the page truthful.

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';
import { parseSSEJson, readSSE } from 'lemma-sdk';
import { getLemmaClient } from '@/lib/sdk/lemma-client';
import type { WorkflowRun } from '@/lib/types';
import { isTerminalWorkflowRunStatus } from './use-flows';

type RunFrame = {
    type?: string;
    data?: unknown;
};

export function useFlowRunStream(
    podId: string | undefined,
    workflowName: string | undefined,
    runId: string | undefined,
    options: { enabled?: boolean } = {}
) {
    const queryClient = useQueryClient();
    const enabled = options.enabled !== false && Boolean(podId && workflowName && runId);

    useEffect(() => {
        if (!enabled || !podId || !workflowName || !runId) return;

        const controller = new AbortController();
        let cancelled = false;

        const consume = async () => {
            try {
                const stream = await getLemmaClient(podId).stream(
                    `/pods/${podId}/workflow-runs/${runId}/stream`,
                    { headers: { Accept: 'text/event-stream' }, signal: controller.signal }
                );

                for await (const raw of readSSE(stream)) {
                    if (cancelled) return;
                    const frame = parseSSEJson<RunFrame>(raw);
                    if (!frame || frame.type === 'error') continue;

                    const run = frame.data as WorkflowRun | undefined;
                    if (!run || typeof run !== 'object') continue;

                    queryClient.setQueryData(['flow-runs', podId, workflowName, runId], run);

                    if (frame.type === 'completed' || isTerminalWorkflowRunStatus(run.status)) {
                        // The list this run appears in is now out of date too.
                        queryClient.invalidateQueries({ queryKey: ['flow-runs', 'infinite', podId, workflowName] });
                        return;
                    }
                }
            } catch {
                // Nothing to report: polling is still running and remains the
                // source of truth. A visible error here would be noise about a
                // transport the reader never asked for.
            }
        };

        void consume();

        return () => {
            cancelled = true;
            controller.abort();
        };
    }, [enabled, podId, queryClient, runId, workflowName]);
}
