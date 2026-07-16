import { describe, expect, it } from 'vitest';

import {
    HOME_WORKSPACE_TAB,
    NEW_WORKSPACE_TAB,
    appWorkspaceTab,
    closeWorkspaceTab,
    conversationWorkspaceTab,
    getActiveWorkspaceTabId,
    getAppSlugFromWorkspaceTab,
    getWorkspaceTabAfterClose,
    parseWorkspaceTabs,
    promoteNewConversationTab,
    routeWorkspaceTab,
    serializeWorkspaceTabs,
    syncPinnedAppTabs,
    upsertWorkspaceTab,
    type PodWorkspaceTab,
} from './workspace-tabs';

describe('pod workspace tabs', () => {
    it('keeps Home pinned while restoring valid, unique tabs', () => {
        const restored = parseWorkspaceTabs(JSON.stringify({
            version: 3,
            tabs: [
                { kind: 'home', resourceId: 'home', title: 'Wrong home' },
                { kind: 'route', resourceId: 'apps', title: 'Stale app route', href: '/pod/pod-1/app/view?page=quote-desk' },
                { kind: 'route', resourceId: 'data', title: 'Projects', href: '/pod/pod-1/data?tab=projects' },
                { kind: 'route', resourceId: 'data', title: 'Duplicate', href: '/pod/pod-1/data?tab=other' },
                { kind: 'conversation', resourceId: 'conv-1', title: 'Pricing follow-up', status: 'running' },
                { kind: 'app', resourceId: 'quote-desk', title: 'Quote Desk', icon: 'Q', url: 'https://quote.example.com' },
                { kind: 'unknown', resourceId: 'ignored', title: 'Ignored' },
            ],
        }));

        expect(restored).toEqual([
            HOME_WORKSPACE_TAB,
            {
                id: 'route:data',
                kind: 'route',
                resourceId: 'data',
                title: 'Projects',
                href: '/pod/pod-1/data?tab=projects',
            },
            {
                id: 'conversation:conv-1',
                kind: 'conversation',
                resourceId: 'conv-1',
                title: 'Pricing follow-up',
                status: null,
            },
            {
                id: 'app:quote-desk',
                kind: 'app',
                resourceId: 'quote-desk',
                title: 'Quote Desk',
                icon: 'Q',
                url: 'https://quote.example.com',
            },
        ]);
    });

    it('migrates the old new-conversation tab into the special New tab', () => {
        const restored = parseWorkspaceTabs(JSON.stringify({
            version: 1,
            tabs: [
                { kind: 'conversation', resourceId: 'new', title: 'New conversation' },
            ],
        }));

        expect(restored).toEqual([HOME_WORKSPACE_TAB, NEW_WORKSPACE_TAB]);
    });

    it('keeps one non-app section tab while its title and href follow navigation', () => {
        const conversation = conversationWorkspaceTab('conv-1', { title: 'First', status: 'completed' });
        const firstDataRoute = routeWorkspaceTab('data', 'Data', '/pod/pod-1/data');
        const tabs = [HOME_WORKSPACE_TAB, firstDataRoute, conversation];
        const updatedDataRoute = routeWorkspaceTab(
            'data',
            'Projects',
            '/pod/pod-1/data?tab=projects',
        );

        expect(upsertWorkspaceTab(tabs, updatedDataRoute)).toEqual([
            HOME_WORKSPACE_TAB,
            updatedDataRoute,
            conversation,
        ]);
    });

    it('pins every installed app before the working set', () => {
        const conversation = conversationWorkspaceTab('conv-1', { title: 'First', status: 'completed' });
        const staleApp = appWorkspaceTab({ slug: 'old-app', title: 'Old App' });
        const staleAppRoute = routeWorkspaceTab(
            'apps',
            'Old App',
            '/pod/pod-1/app/view?page=old-app',
        );
        const pages = [
            { slug: 'morning-brief', title: 'Morning Brief', icon: 'M', order: 0, path: '' },
            { slug: 'quote-desk', title: 'Quote Desk', icon: 'Q', order: 1, path: '' },
        ];

        expect(syncPinnedAppTabs(
            [HOME_WORKSPACE_TAB, staleApp, conversation, staleAppRoute],
            pages,
        )).toEqual([
            HOME_WORKSPACE_TAB,
            appWorkspaceTab(pages[0]),
            appWorkspaceTab(pages[1]),
            conversation,
        ]);
    });

    it('extracts the current app from a pinned app tab', () => {
        expect(getAppSlugFromWorkspaceTab(appWorkspaceTab({
            slug: 'quote-desk',
            title: 'Quote Desk',
        }))).toBe('quote-desk');
        expect(getAppSlugFromWorkspaceTab(
            routeWorkspaceTab('data', 'Projects', '/pod/pod-1/data/projects'),
        )).toBeNull();
    });

    it('promotes the temporary new-conversation tab in place', () => {
        const app = appWorkspaceTab({ slug: 'quote-desk', title: 'Quote Desk' });
        const temporary = NEW_WORKSPACE_TAB;
        const tabs = [HOME_WORKSPACE_TAB, app, temporary];

        expect(promoteNewConversationTab(
            tabs,
            'conv-2',
            { title: 'Review the quote', status: 'running' },
        )).toEqual([
            HOME_WORKSPACE_TAB,
            app,
            {
                id: 'conversation:conv-2',
                kind: 'conversation',
                resourceId: 'conv-2',
                title: 'Review the quote',
                status: 'running',
            },
        ]);
    });

    it('chooses the adjacent tab when the active tab closes', () => {
        const first = conversationWorkspaceTab('conv-1');
        const second = conversationWorkspaceTab('conv-2');
        const tabs = [HOME_WORKSPACE_TAB, first, second];

        expect(getWorkspaceTabAfterClose(tabs, first.id)).toBe(second);
        expect(getWorkspaceTabAfterClose(tabs, second.id)).toBe(first);
        expect(closeWorkspaceTab(tabs, HOME_WORKSPACE_TAB.id)).toBe(tabs);
        expect(closeWorkspaceTab([HOME_WORKSPACE_TAB, appWorkspaceTab({
            slug: 'quote-desk',
            title: 'Quote Desk',
        })], 'app:quote-desk')).toEqual([
            HOME_WORKSPACE_TAB,
            appWorkspaceTab({ slug: 'quote-desk', title: 'Quote Desk' }),
        ]);
    });

    it('uses routes as the active-tab source of truth', () => {
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1')).toBe('home');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/app/view', 'quote-desk')).toBe('app:quote-desk');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/app/view')).toBe('route:apps');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/app/pages')).toBe('route:apps');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/data/projects')).toBe('route:data');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/datastores/default')).toBe('route:data');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/docs')).toBe('route:files');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/channels')).toBe('route:surfaces');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/flows')).toBe('route:workflows');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/conversations/new')).toBe('new');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/conversations/conv%201')).toBe('conversation:conv 1');
        expect(getActiveWorkspaceTabId('pod-1', '/pod/pod-1/settings')).toBe('route:settings');
    });

    it('does not persist stale conversation activity', () => {
        const tabs: PodWorkspaceTab[] = [
            HOME_WORKSPACE_TAB,
            conversationWorkspaceTab('conv-1', { title: 'Running', status: 'running' }),
        ];

        expect(parseWorkspaceTabs(serializeWorkspaceTabs(tabs))[1]).toEqual({
            id: 'conversation:conv-1',
            kind: 'conversation',
            resourceId: 'conv-1',
            title: 'Running',
            status: null,
        });
    });
});
