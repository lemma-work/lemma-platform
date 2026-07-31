'use client';

import { useEffect } from 'react';
import { useQueryClient } from '@tanstack/react-query';

import { playSoundFeedback } from '@/lib/feedback/sound-feedback';
import {
    extractWorkflowRunFeedbackSnapshots,
    getWorkflowFeedbackEvent,
    type WorkflowRunFeedbackSnapshot,
} from '@/lib/feedback/workflow-feedback';

function isConversationStageFrame() {
    if (typeof window === 'undefined' || window.self === window.top) return false;
    return new URLSearchParams(window.location.search).get('embed') === 'conversation-stage';
}

export function SoundFeedbackRuntime() {
    const queryClient = useQueryClient();

    useEffect(() => {
        if (isConversationStageFrame()) return;

        const runsById = new Map<string, WorkflowRunFeedbackSnapshot>();
        const initializedQueries = new Set<string>();
        const loadFailures = new Map<string, number>();

        const processQuery = (query: {
            queryHash: string;
            queryKey: readonly unknown[];
            state: { data: unknown };
        }) => {
            const snapshots = extractWorkflowRunFeedbackSnapshots(query.queryKey, query.state.data);
            if (snapshots.length === 0) return;

            const initialized = initializedQueries.has(query.queryHash);
            initializedQueries.add(query.queryHash);

            for (const snapshot of snapshots) {
                const previous = runsById.get(snapshot.id);
                if (previous && snapshot.timestamp > 0 && previous.timestamp > snapshot.timestamp) continue;

                runsById.set(snapshot.id, snapshot);
                if (!initialized) continue;

                if (!previous) {
                    const ageMs = snapshot.timestamp > 0 ? Math.abs(Date.now() - snapshot.timestamp) : Infinity;
                    const event = ageMs <= 30_000
                        ? snapshot.state === 'active'
                            ? 'work-start'
                            : snapshot.state === 'waiting'
                                ? 'work-waiting'
                                : null
                        : null;
                    if (event) {
                        playSoundFeedback(event, {
                            onceKey: snapshot.state === 'active'
                                ? `workflow:${snapshot.id}:started`
                                : `workflow:${snapshot.id}:${snapshot.state}:${snapshot.timestamp}`,
                        });
                    }
                    continue;
                }

                const event = getWorkflowFeedbackEvent(previous.state, snapshot.state);
                if (event) {
                    playSoundFeedback(event, {
                        onceKey: `workflow:${snapshot.id}:${snapshot.state}:${snapshot.timestamp}`,
                    });
                }
            }
        };

        queryClient.getQueryCache().getAll().forEach(processQuery);

        return queryClient.getQueryCache().subscribe((event) => {
            if (!('query' in event) || !event.query) return;

            processQuery(event.query);

            const { query } = event;
            if (query.state.status !== 'error' || typeof query.state.data !== 'undefined') return;
            const errorUpdatedAt = query.state.errorUpdatedAt;
            if (loadFailures.get(query.queryHash) === errorUpdatedAt) return;
            loadFailures.set(query.queryHash, errorUpdatedAt);
            playSoundFeedback('load-failure', {
                onceKey: `query:${query.queryHash}:${errorUpdatedAt}`,
            });
        });
    }, [queryClient]);

    return null;
}
