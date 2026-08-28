import { describe, expect, it } from 'vitest';

import {
    appPageSlugFromRouteParam,
    createUniqueAppPageSlug,
    normalizeAppPageSlug,
} from './app-page-slugs';

/** How the app index slugs a page, for names arriving in list order. */
function indexSlugs(names: string[]): string[] {
    const taken: string[] = [];
    return names.map((name) => {
        const slug = createUniqueAppPageSlug({
            title: name,
            preferredSlug: name,
            existingSlugs: taken,
        });
        taken.push(slug);
        return slug;
    });
}

describe('appPageSlugFromRouteParam', () => {
    it('reads a page slug that is already canonical', () => {
        expect(appPageSlugFromRouteParam('expense-tracker')).toBe('expense-tracker');
        expect(appPageSlugFromRouteParam('ledger-2')).toBe('ledger-2');
    });

    it('names nothing when the route names nothing', () => {
        expect(appPageSlugFromRouteParam(null)).toBeNull();
        expect(appPageSlugFromRouteParam(undefined)).toBeNull();
        expect(appPageSlugFromRouteParam('')).toBeNull();
        expect(appPageSlugFromRouteParam('   ')).toBeNull();
    });

    it('resolves an app name to the page slug the index gave it', () => {
        // The invariant the workspace depends on: whichever spelling of an app a
        // link carries — the resource name a person or an agent writes, or the
        // slug the index publishes — both address the same page. Without it a
        // `display_resource` link landed on a page no entry has, and an app that
        // had just been built read as "App unavailable".
        const names = ['Expense Tracker', 'Quote_Desk', 'Ledger 2.0'];
        const slugs = indexSlugs(names);

        expect(names.map((name) => appPageSlugFromRouteParam(name))).toEqual(slugs);
    });

    it('resolves both apps that share a name to a page, and the first of them', () => {
        // The index breaks a tie by order, so the second `Ledger` is `ledger-2`.
        // A link that carries only the name cannot say which was meant; it opens
        // the first, which is the same app the sidebar's first `Ledger` row does.
        const slugs = indexSlugs(['Ledger', 'ledger']);
        expect(slugs).toEqual(['ledger', 'ledger-2']);
        expect(appPageSlugFromRouteParam('Ledger')).toBe('ledger');
    });

    it('is idempotent, so a canonicalized route param survives another pass', () => {
        const slug = appPageSlugFromRouteParam('Expense Tracker')!;
        expect(normalizeAppPageSlug(slug)).toBe(slug);
        expect(appPageSlugFromRouteParam(slug)).toBe(slug);
    });
});
