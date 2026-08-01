import { describe, expect, it } from 'vitest';

import {
    EMPTY_POD_START_SIGNALS,
    buildPodDoActions,
    buildPodFacts,
    resolvePodStartMode,
    type PodStartSignals,
} from './pod-start-signals';

function signals(overrides: Partial<PodStartSignals> = {}): PodStartSignals {
    return { ...EMPTY_POD_START_SIGNALS, ...overrides };
}

describe('resolvePodStartMode', () => {
    it('reads an untouched pod as fresh', () => {
        expect(resolvePodStartMode(signals())).toBe('fresh');
    });

    it('agrees with Home once a pod is operating', () => {
        const mode = resolvePodStartMode(signals({
            agents: [{ name: 'triage' }],
            workflows: [{ name: 'digest' }],
            appCount: 1,
        }));
        expect(mode).toBe('operating');
    });
});

describe('buildPodFacts', () => {
    it('says nothing about resources the pod does not have', () => {
        expect(buildPodFacts(signals({ tables: [{ name: 'invoices' }] }))).toEqual(['1 table']);
    });

    it('pluralizes and caps the line', () => {
        const facts = buildPodFacts(signals({
            tables: [{ name: 'a' }, { name: 'b' }],
            agents: [{ name: 'x' }],
            workflows: [{ name: 'w1' }, { name: 'w2' }, { name: 'w3' }],
            appCount: 2,
            activeSurfaceCount: 1,
            scheduleCount: 4,
        }));

        expect(facts).toEqual(['2 tables', '1 agent', '3 workflows', '2 apps']);
    });
});

describe('buildPodDoActions', () => {
    it('offers nothing for an empty pod', () => {
        expect(buildPodDoActions(signals())).toEqual([]);
    });

    it('keeps catalog order and keeps prompts sendable on their own', () => {
        const actions = buildPodDoActions(signals({
            tables: [
                { name: 'invoices' },
                { name: 'notes' },
                { name: 'archive' },
            ],
        }));

        expect(actions.map((action) => action.label)).toEqual([
            'Review Invoices',
            'Review Notes',
            'Review Archive',
        ]);
        expect(actions[0].prompt).toContain('invoices table');
        expect(actions[0].prompt.endsWith('.')).toBe(true);
    });

    it('caps a large pod so the panel stays one screen of choices', () => {
        const actions = buildPodDoActions(signals({
            tables: Array.from({ length: 9 }, (_, index) => ({ name: `table_${index}` })),
            workflows: Array.from({ length: 9 }, (_, index) => ({ name: `flow_${index}` })),
        }));

        expect(actions).toHaveLength(8);
        expect(actions.filter((action) => action.id.startsWith('workflow:'))).toHaveLength(4);
    });

    it('names the raw resource in the prompt but the readable one on the chip', () => {
        const [action] = buildPodDoActions(signals({ workflows: [{ name: 'weekly_digest' }] }));

        expect(action.label).toBe('Run Weekly Digest');
        expect(action.prompt).toContain('weekly_digest workflow');
    });

    it('keeps a lone workflow visible next to tables', () => {
        const actions = buildPodDoActions(signals({
            tables: [
                { name: 'invoices' },
                { name: 'notes' },
            ],
            workflows: [{ name: 'weekly_digest' }],
        }));

        expect(actions.map((action) => action.label)).toEqual([
            'Review Invoices',
            'Run Weekly Digest',
            'Review Notes',
        ]);
    });
});
