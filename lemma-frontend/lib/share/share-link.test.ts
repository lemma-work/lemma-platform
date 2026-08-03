import { describe, expect, it } from 'vitest';

import {
    buildShareLink,
    isShareKind,
    prettifySlug,
    resolveShareDestination,
    resolveShareName,
    resolveShareTarget,
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

    it('names a table from the same query key the target is addressed by', () => {
        // `resolveShareTarget` reads a table's name from `tab`; this used to look
        // for `table`, so a table link silently fell back to its path slug.
        const query = { tab: 'open_orders' };
        expect(resolveShareTarget('table', ['pod', 'p1', 'tables'], query)).toEqual({
            podId: 'p1',
            resourceType: 'datastore_table',
            resourceName: 'open_orders',
        });
        expect(resolveShareName({ segments: ['pod', 'p1', 'tables'], query })).toBe('Open Orders');
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

describe('resolveShareTarget', () => {
    const podPath = ['pod', 'p1'];

    it('addresses a document by id', () => {
        expect(resolveShareTarget('document', [...podPath, 'files'], { fileId: 'f-1' })).toEqual({
            podId: 'p1',
            resourceType: 'document',
            resourceId: 'f-1',
        });
    });

    it('still resolves documents from links minted before ids', () => {
        expect(resolveShareTarget('document', [...podPath, 'files'], { file: '/notes.md' })).toEqual({
            podId: 'p1',
            resourceType: 'document',
            resourceName: '/notes.md',
        });
    });

    it('takes named resources from the last path segment', () => {
        expect(resolveShareTarget('agent', [...podPath, 'agents', 'support-triage'], {})).toEqual({
            podId: 'p1',
            resourceType: 'agent',
            resourceName: 'support-triage',
        });
    });

    it('decodes a name that needed escaping in the path', () => {
        expect(
            resolveShareTarget('workflow', [...podPath, 'flows', 'nightly%20sync'], {}),
        ).toMatchObject({ resourceName: 'nightly sync' });
    });

    it('takes a table from the query key the data route actually uses', () => {
        expect(resolveShareTarget('table', [...podPath, 'data'], { tab: 'orders' })).toEqual({
            podId: 'p1',
            resourceType: 'datastore_table',
            resourceName: 'orders',
        });
    });

    it('maps kinds onto the backend resource-type vocabulary', () => {
        expect(
            resolveShareTarget('table', [...podPath, 'data'], { tab: 't' })?.resourceType,
        ).toBe('datastore_table');
    });

    it('returns null for a pod link, which names no resource', () => {
        expect(resolveShareTarget('pod', podPath, {})).toBeNull();
    });

    it('returns null when the identity is missing or the path is not a pod', () => {
        expect(resolveShareTarget('document', [...podPath, 'files'], {})).toBeNull();
        expect(resolveShareTarget('agent', ['not-pod', 'p1', 'agents', 'a'], {})).toBeNull();
        expect(resolveShareTarget('agent', [], {})).toBeNull();
    });
});
