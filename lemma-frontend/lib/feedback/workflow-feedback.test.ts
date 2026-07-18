import { describe, expect, it } from 'vitest';

import {
    extractWorkflowRunFeedbackSnapshots,
    getWorkflowFeedbackEvent,
    getWorkflowRunFeedbackState,
} from './workflow-feedback';

describe('workflow sound feedback', () => {
    it('normalizes workflow lifecycle statuses', () => {
        expect(getWorkflowRunFeedbackState('IN_PROGRESS')).toBe('active');
        expect(getWorkflowRunFeedbackState('waiting_for_input')).toBe('waiting');
        expect(getWorkflowRunFeedbackState('succeeded')).toBe('complete');
        expect(getWorkflowRunFeedbackState('ERROR')).toBe('failed');
        expect(getWorkflowRunFeedbackState('cancelled')).toBe('cancelled');
    });

    it('only sounds meaningful observed transitions', () => {
        expect(getWorkflowFeedbackEvent('active', 'waiting')).toBe('work-waiting');
        expect(getWorkflowFeedbackEvent('waiting', 'active')).toBe('work-start');
        expect(getWorkflowFeedbackEvent('active', 'complete')).toBe('work-complete');
        expect(getWorkflowFeedbackEvent('waiting', 'failed')).toBe('work-fail');
        expect(getWorkflowFeedbackEvent(null, 'complete')).toBeNull();
        expect(getWorkflowFeedbackEvent('active', 'cancelled')).toBeNull();
    });

    it('extracts runs from list, snapshot, infinite, and wait query shapes', () => {
        const run = {
            id: 'run-1',
            status: 'RUNNING',
            updated_at: '2026-07-17T10:00:00.000Z',
        };

        expect(extractWorkflowRunFeedbackSnapshots(['flow-runs'], [run])).toHaveLength(1);
        expect(extractWorkflowRunFeedbackSnapshots(
            ['workflow-run-snapshots'],
            [{ workflowName: 'daily', runs: [run] }],
        )).toHaveLength(1);
        expect(extractWorkflowRunFeedbackSnapshots(
            ['flow-runs', 'infinite'],
            { pages: [{ items: [run] }] },
        )).toHaveLength(1);
        expect(extractWorkflowRunFeedbackSnapshots(
            ['workflow-run-waits'],
            { items: [{ run: { ...run, status: 'WAITING' }, wait: {} }] },
        )[0]).toMatchObject({ id: 'run-1', state: 'waiting' });
    });

    it('ignores data from unrelated queries', () => {
        expect(extractWorkflowRunFeedbackSnapshots(
            ['records'],
            [{ id: 'record-1', status: 'RUNNING' }],
        )).toEqual([]);
    });
});
