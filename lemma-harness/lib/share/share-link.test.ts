import { describe, expect, it } from 'vitest';

import {
    buildContactLink,
    buildShareLink,
    isShareKind,
    humanizeResourceName,
    prettifySlug,
    resolveShareDestination,
    resolveShareName,
    resolveSharePodId,
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

    it('names the pod behind a link that points at no resource', () => {
        // A pod link has no target — there is nothing inside it to preview — but
        // the landing page still needs the pod so it can offer a way in rather
        // than redirecting a non-member into an access wall.
        expect(resolveShareTarget('pod', ['pod', 'p1'])).toBeNull();
        expect(resolveSharePodId(['pod', 'p1'])).toBe('p1');
        expect(resolveSharePodId(['pod', 'p1', 'agents', 'triage'])).toBe('p1');
        expect(resolveSharePodId(['pod'])).toBeNull();
        expect(resolveSharePodId(undefined)).toBeNull();
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

describe('humanizeResourceName', () => {
    it('reads a filesystem name as a title', () => {
        expect(humanizeResourceName('annamacharya-report.html')).toBe('Annamacharya report.html');
        expect(humanizeResourceName('meeting_minutes.md')).toBe('Meeting minutes.md');
    });

    it('takes the file, not the folders above it', () => {
        expect(humanizeResourceName('/library/q3/quarterly-review.pdf'))
            .toBe('Quarterly review.pdf');
    });

    it('raises only the first letter', () => {
        // Title Case On Every Word reads as a headline someone wrote. This is a
        // name someone typed, and the rest of it is theirs.
        expect(humanizeResourceName('notes-on-the-BOM-encoding.txt'))
            .toBe('Notes on the BOM encoding.txt');
    });

    it('leaves a name that is already a name alone', () => {
        expect(humanizeResourceName('README.md')).toBe('README.md');
        expect(humanizeResourceName('Orders')).toBe('Orders');
    });

    it('collapses runs of separators rather than leaving a gap', () => {
        expect(humanizeResourceName('draft__v2--final.docx')).toBe('Draft v2 final.docx');
    });

    it('keeps a name it cannot improve', () => {
        expect(humanizeResourceName('')).toBe('');
        expect(humanizeResourceName('/')).toBe('/');
    });
});

describe('contact links', () => {
    const card = {
        name: 'Support Triage',
        seed: 'a1b2c3d4',
        telegram: '@support_triage_bot',
        whatsapp: '+15551234567',
        email: 'triage@pod.lemma.work',
    };

    it('is a share link with the card riding along', () => {
        const link = buildContactLink({
            canonicalUrl: 'https://lemma.work/pod/p1/agents/support_triage',
            card,
        });
        const url = new URL(link!);

        expect(url.pathname).toBe('/s/contact/pod/p1/agents/support_triage');
        expect(url.searchParams.get('n')).toBe('Support Triage');
        expect(url.searchParams.get('tg')).toBe('@support_triage_bot');
        expect(url.searchParams.get('sd')).toBe('a1b2c3d4');
    });

    it('refuses a URL that is not a workspace one', () => {
        expect(buildContactLink({ canonicalUrl: 'https://evil.example/pod/p1', card })).not.toBeNull();
        expect(buildContactLink({ canonicalUrl: 'https://lemma.work/blog/x', card })).toBeNull();
        expect(buildContactLink({ canonicalUrl: 'not a url', card })).toBeNull();
    });

    it('points at an agent, so a reader can ask whether they may see it', () => {
        expect(resolveShareTarget('contact', ['pod', 'p1', 'agents', 'support_triage'])).toEqual({
            podId: 'p1',
            resourceType: 'agent',
            resourceName: 'support_triage',
        });
    });

    it('is a kind the router will accept', () => {
        expect(isShareKind('contact')).toBe(true);
    });

    /*
     * The card's params describe the picture on the share page and mean nothing
     * to the workspace. Left in, an "Open it in Lemma" click would carry the
     * agent's phone number into a member's address bar on the way to a page with
     * no use for it.
     */
    it('leaves the card behind when it hands over to the workspace', () => {
        const destination = resolveShareDestination(['pod', 'p1', 'agents', 'support_triage'], {
            n: 'Support Triage',
            tg: '@support_triage_bot',
            wa: '+15551234567',
            em: 'triage@pod.lemma.work',
            sd: 'a1b2c3d4',
            ic: 'lemma-identity:3',
            o: 'Acme',
            d: 'Answers mail.',
            tab: 'orders',
        });

        expect(destination).toBe('/pod/p1/agents/support_triage?tab=orders');
    });
});
