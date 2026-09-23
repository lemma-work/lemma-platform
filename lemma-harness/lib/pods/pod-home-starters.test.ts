import { describe, expect, it } from 'vitest';

import { resolvePodHomeStarterMode, type PodHomeResourceSignals } from './pod-home-starters';

const EMPTY: PodHomeResourceSignals = {
    appCount: 0,
    agentCount: 0,
    workflowCount: 0,
    surfaceCount: 0,
    activeSurfaceCount: 0,
    scheduleCount: 0,
    conversationCount: 0,
    hasUsedWorkflow: false,
};

describe('resolvePodHomeStarterMode', () => {
    it('shows the full starter experience for a truly fresh pod', () => {
        expect(resolvePodHomeStarterMode(EMPTY)).toBe('fresh');
    });

    it('keeps a conversation-only or single-resource pod in the forming state', () => {
        expect(resolvePodHomeStarterMode({ ...EMPTY, conversationCount: 1 })).toBe('forming');
        expect(resolvePodHomeStarterMode({ ...EMPTY, agentCount: 1 })).toBe('forming');
        expect(resolvePodHomeStarterMode({ ...EMPTY, appCount: 1 })).toBe('forming');
    });

    it('treats a working app or live channel pairing as operating', () => {
        expect(resolvePodHomeStarterMode({ ...EMPTY, appCount: 1, agentCount: 1 })).toBe('operating');
        expect(resolvePodHomeStarterMode({ ...EMPTY, agentCount: 1, surfaceCount: 1, activeSurfaceCount: 1 })).toBe('operating');
    });

    it('treats scheduled or already-used workflows as operating', () => {
        expect(resolvePodHomeStarterMode({ ...EMPTY, workflowCount: 1, scheduleCount: 1 })).toBe('operating');
        expect(resolvePodHomeStarterMode({ ...EMPTY, workflowCount: 1, hasUsedWorkflow: true })).toBe('operating');
    });

    it('uses three durable resources as the fallback operating threshold', () => {
        expect(resolvePodHomeStarterMode({ ...EMPTY, agentCount: 2, workflowCount: 1 })).toBe('operating');
        expect(resolvePodHomeStarterMode({ ...EMPTY, surfaceCount: 1, conversationCount: 8 })).toBe('forming');
    });
});
