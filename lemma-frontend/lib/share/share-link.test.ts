import { describe, expect, it } from 'vitest';

import {
    buildShareLink,
    isShareKind,
    prettifySlug,
    resolveShareDestination,
    resolveShareName,
    shareKindForResourceType,
} from './share-link';

describe('share links', () => {
    it('wraps a canonical workspace url and carries the display name', () => {
        expect(
            buildShareLink({
                kind: 'agent',
                canonicalUrl: 'https://lemma.work/pod/p1/agents/support-triage',
                name: 'Support Triage',
            }),
        ).toBe('https://lemma.work/s/agent/pod/p1/agents/support-triage?n=Support+Triage');
    });

    it('preserves the query that identifies apps, tables and documents', () => {
        expect(
            buildShareLink({
                kind: 'app',
                canonicalUrl: 'https://lemma.work/pod/p1/app/view?page=taskflow',
                name: 'Taskflow',
            }),
        ).toBe('https://lemma.work/s/app/pod/p1/app/view?page=taskflow&n=Taskflow');
    });

    it('refuses to wrap anything that is not a workspace url', () => {
        expect(
            buildShareLink({ kind: 'agent', canonicalUrl: 'https://lemma.work/docs/agents' }),
        ).toBeNull();
        expect(buildShareLink({ kind: 'agent', canonicalUrl: 'not a url' })).toBeNull();
    });

    it('round trips back to the workspace path', () => {
        expect(resolveShareDestination(['pod', 'p1', 'agents', 'support-triage'])).toBe(
            '/pod/p1/agents/support-triage',
        );
        expect(
            resolveShareDestination(['pod', 'p1', 'app', 'view'], { page: 'taskflow', n: 'Taskflow' }),
        ).toBe('/pod/p1/app/view?page=taskflow');
    });

    it('never resolves to an off-origin or non-workspace destination', () => {
        expect(resolveShareDestination(['', '', 'evil.com'])).toBeNull();
        expect(resolveShareDestination(['docs', 'agents'])).toBeNull();
        expect(resolveShareDestination(['pod'])).toBeNull();
        expect(resolveShareDestination([])).toBeNull();
        expect(resolveShareDestination(undefined)).toBeNull();
    });

    it('names the card from the link alone', () => {
        expect(resolveShareName({ name: 'Support Triage' })).toBe('Support Triage');
        expect(resolveShareName({ segments: ['pod', 'p1', 'agents', 'support-triage'] })).toBe(
            'Support Triage',
        );
        expect(
            resolveShareName({ segments: ['pod', 'p1', 'app', 'view'], query: { page: 'task_flow' } }),
        ).toBe('Task Flow');
        expect(resolveShareName({ segments: ['pod', 'p1'] })).toBe('P1');
    });

    it('prettifies slugs without dragging extensions along', () => {
        expect(prettifySlug('quarterly-report.md')).toBe('Quarterly Report');
        expect(prettifySlug('notes/meeting_minutes')).toBe('Meeting Minutes');
        expect(prettifySlug('')).toBe('');
    });

    it('maps the share dialog vocabulary onto url-friendly kinds', () => {
        expect(shareKindForResourceType('datastore_table')).toBe('table');
        expect(shareKindForResourceType('agent')).toBe('agent');
        expect(isShareKind('table')).toBe(true);
        expect(isShareKind('datastore_table')).toBe(false);
        expect(isShareKind(null)).toBe(false);
    });
});
