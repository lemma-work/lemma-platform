import type { Conversation } from '@/lib/types';
import type { AppPageRef } from '@/lib/types/app';
import { buildConversationStandaloneResourceHref } from '@/lib/assistant/conversation-presentation';

const WORKSPACE_TABS_VERSION = 3;
const MAX_PERSISTED_TABS = 50;

export type PodWorkspaceTab =
    | {
        id: 'home';
        kind: 'home';
        resourceId: 'home';
        title: 'Home';
      }
    | {
        id: 'new';
        kind: 'new';
        resourceId: 'new';
        title: 'New';
      }
    | {
        id: `app:${string}`;
        kind: 'app';
        resourceId: string;
        title: string;
        icon?: string | null;
        url?: string | null;
      }
    | {
        id: `conversation:${string}`;
        kind: 'conversation';
        resourceId: string;
        title: string;
        status?: string | null;
      }
    | {
        id: `route:${string}`;
        kind: 'route';
        resourceId: string;
        title: string;
        href: string;
      };

export type HomeWorkspaceTab = Extract<PodWorkspaceTab, { kind: 'home' }>;
export type NewWorkspaceTab = Extract<PodWorkspaceTab, { kind: 'new' }>;
export type AppWorkspaceTab = Extract<PodWorkspaceTab, { kind: 'app' }>;
export type ConversationWorkspaceTab = Extract<PodWorkspaceTab, { kind: 'conversation' }>;
export type RouteWorkspaceTab = Extract<PodWorkspaceTab, { kind: 'route' }>;

export const HOME_WORKSPACE_TAB: HomeWorkspaceTab = {
    id: 'home',
    kind: 'home',
    resourceId: 'home',
    title: 'Home',
};

export const NEW_WORKSPACE_TAB: NewWorkspaceTab = {
    id: 'new',
    kind: 'new',
    resourceId: 'new',
    title: 'New',
};

function cleanLabel(value: unknown, fallback: string) {
    if (typeof value !== 'string') return fallback;
    const cleaned = value.replace(/\s+/g, ' ').trim();
    return cleaned ? cleaned.slice(0, 200) : fallback;
}

export function formatWorkspaceAppTitle(value: string | null | undefined) {
    const cleaned = (value || '').replace(/[_-]+/g, ' ').replace(/\s+/g, ' ').trim();
    if (!cleaned) return 'Untitled app';
    return cleaned
        .split(' ')
        .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
        .join(' ');
}

export function appWorkspaceTab(
    page: Pick<AppPageRef, 'slug' | 'title' | 'icon' | 'url'>,
): AppWorkspaceTab {
    return {
        id: `app:${page.slug}`,
        kind: 'app',
        resourceId: page.slug,
        title: formatWorkspaceAppTitle(page.title || page.slug),
        icon: page.icon ?? null,
        url: page.url ?? null,
    };
}

export function conversationWorkspaceTab(
    conversationId: string,
    conversation?: Pick<Conversation, 'title' | 'status'> | null,
): ConversationWorkspaceTab {
    return {
        id: `conversation:${conversationId}`,
        kind: 'conversation',
        resourceId: conversationId,
        title: cleanLabel(conversation?.title, 'Untitled conversation'),
        status: conversation?.status ?? null,
    };
}

function safeWorkspaceHref(value: string) {
    if (!value.startsWith('/pod/')) return '/';
    return buildConversationStandaloneResourceHref(value) ?? '/';
}

const WIDGET_VIEW_SUFFIX = '/widgets/view';

/**
 * Route tabs are keyed by section, so a section opens one tab and navigating
 * within it moves that tab along — one Data tab, one Files tab. A presented
 * widget is not a section: each one is its own artifact, and two of them are
 * two things to hold open. Key those by the tool call that produced them so
 * opening a second widget adds a tab instead of overwriting the first.
 */
export function widgetWorkspaceRouteKey(toolCallId: string) {
    const cleaned = toolCallId.replace(/[^a-z0-9_-]+/gi, '-').replace(/^-+|-+$/g, '');
    return cleaned ? `widget-${cleaned}` : 'widgets';
}

export function isWidgetWorkspaceRouteKey(routeKey: string) {
    return routeKey.startsWith('widget-');
}

export function routeWorkspaceTab(
    routeKey: string,
    title: string,
    href: string,
): RouteWorkspaceTab {
    const resourceId = routeKey.replace(/[^a-z0-9_-]+/gi, '-').replace(/^-+|-+$/g, '') || 'workspace';
    return {
        id: `route:${resourceId}`,
        kind: 'route',
        resourceId,
        title: cleanLabel(title, formatWorkspaceAppTitle(resourceId)),
        href: safeWorkspaceHref(href),
    };
}

function tabsEqual(left: PodWorkspaceTab, right: PodWorkspaceTab) {
    return left.id === right.id
        && left.kind === right.kind
        && left.resourceId === right.resourceId
        && left.title === right.title
        && ('icon' in left ? left.icon : undefined) === ('icon' in right ? right.icon : undefined)
        && ('url' in left ? left.url : undefined) === ('url' in right ? right.url : undefined)
        && ('status' in left ? left.status : undefined) === ('status' in right ? right.status : undefined)
        && ('href' in left ? left.href : undefined) === ('href' in right ? right.href : undefined);
}

export function upsertWorkspaceTab(tabs: PodWorkspaceTab[], tab: PodWorkspaceTab) {
    const existingIndex = tabs.findIndex((candidate) => candidate.id === tab.id);
    if (existingIndex === -1) return [...tabs, tab];
    if (tabsEqual(tabs[existingIndex], tab)) return tabs;

    const next = [...tabs];
    next[existingIndex] = tab;
    return next;
}

export function closeWorkspaceTab(tabs: PodWorkspaceTab[], tabId: string) {
    // Home is the only tab that cannot be closed.
    if (tabId === HOME_WORKSPACE_TAB.id) return tabs;
    const next = tabs.filter((tab) => tab.id !== tabId);
    return next.length === tabs.length ? tabs : next;
}

/**
 * Apps are not tabs — they live in the sidebar rail, one click from anywhere.
 *
 * The strip used to pin every installed app the moment its pages loaded, and
 * `closeWorkspaceTab` refused to close them: a second, permanent copy of the
 * apps index that grew with the pod. Then it held only apps someone had
 * opened. Now the rail answers "what apps does this pod have" and marks the
 * one you are on, so the strip drops app tabs outright — the legacy pinned
 * ones, and the leftover route tabs aimed at the viewer that duplicated
 * them. It never opens anything.
 */
export function syncAppWorkspaceTabs(tabs: PodWorkspaceTab[]) {
    const kept = tabs.filter((tab) => {
        if (tab.kind === 'home') return false;
        if (tab.kind === 'app') return false;
        // A leftover route tab aimed at the app viewer duplicated the app tab.
        if (tab.kind === 'route' && tab.resourceId === 'apps' && tab.href.includes('/app/view')) return false;
        return true;
    });

    const next: PodWorkspaceTab[] = [HOME_WORKSPACE_TAB, ...kept];
    if (next.length !== tabs.length) return next;
    return next.every((tab, index) => tabsEqual(tab, tabs[index])) ? tabs : next;
}

export function getWorkspaceTabAfterClose(tabs: PodWorkspaceTab[], tabId: string) {
    const closingIndex = tabs.findIndex((tab) => tab.id === tabId);
    if (closingIndex === -1) return HOME_WORKSPACE_TAB;
    return tabs[closingIndex + 1] ?? tabs[closingIndex - 1] ?? HOME_WORKSPACE_TAB;
}

export function promoteNewConversationTab(
    tabs: PodWorkspaceTab[],
    conversationId: string,
    conversation?: Pick<Conversation, 'title' | 'status'> | null,
) {
    if (!conversationId || conversationId === 'new') return tabs;

    const temporaryId = NEW_WORKSPACE_TAB.id;
    const temporaryIndex = tabs.findIndex((tab) => tab.id === temporaryId);
    if (temporaryIndex === -1) return tabs;

    const promoted = conversationWorkspaceTab(conversationId, conversation);
    const existingIndex = tabs.findIndex((tab) => tab.id === promoted.id);
    if (existingIndex !== -1) {
        const withoutTemporary = tabs.filter((tab) => tab.id !== temporaryId);
        return upsertWorkspaceTab(withoutTemporary, promoted);
    }

    const next = [...tabs];
    next[temporaryIndex] = promoted;
    return next;
}

export function syncWorkspaceTabMetadata(
    tabs: PodWorkspaceTab[],
    conversations: Conversation[],
) {
    let changed = false;
    const next = tabs.map((tab) => {
        let synced = tab;
        if (tab.kind === 'conversation' && tab.resourceId !== 'new') {
            const conversation = conversations.find((candidate) => candidate.id === tab.resourceId);
            if (conversation) synced = conversationWorkspaceTab(tab.resourceId, conversation);
        }

        if (!tabsEqual(tab, synced)) changed = true;
        return synced;
    });
    return changed ? next : tabs;
}

export function getPodWorkspaceTabsStorageKey(podId: string) {
    return `lemma:pod-workspace-tabs:${podId}`;
}

export function serializeWorkspaceTabs(tabs: PodWorkspaceTab[]) {
    return JSON.stringify({
        version: WORKSPACE_TABS_VERSION,
        tabs: tabs.map((tab) => {
            if (tab.kind === 'conversation') {
                return {
                    id: tab.id,
                    kind: tab.kind,
                    resourceId: tab.resourceId,
                    title: tab.title,
                };
            }
            return tab;
        }),
    });
}

export function parseWorkspaceTabs(value: string | null): PodWorkspaceTab[] {
    if (!value) return [HOME_WORKSPACE_TAB];

    try {
        const parsed = JSON.parse(value) as unknown;
        const items = Array.isArray(parsed)
            ? parsed
            : parsed && typeof parsed === 'object' && Array.isArray((parsed as { tabs?: unknown }).tabs)
                ? (parsed as { tabs: unknown[] }).tabs
                : [];
        const tabs: PodWorkspaceTab[] = [HOME_WORKSPACE_TAB];
        const seen = new Set<string>([HOME_WORKSPACE_TAB.id]);

        for (const item of items.slice(0, MAX_PERSISTED_TABS)) {
            if (!item || typeof item !== 'object') continue;
            const candidate = item as Record<string, unknown>;
            const kind = candidate.kind;
            const resourceId = typeof candidate.resourceId === 'string' ? candidate.resourceId.trim() : '';
            if (kind === 'new' || (kind === 'conversation' && resourceId === 'new')) {
                if (!seen.has(NEW_WORKSPACE_TAB.id)) {
                    seen.add(NEW_WORKSPACE_TAB.id);
                    tabs.push(NEW_WORKSPACE_TAB);
                }
                continue;
            }
            // App tabs from before the rail existed are dropped at the door:
            // they are not restored, and `syncAppWorkspaceTabs` clears any
            // that arrive by another path.
            if (!resourceId || (kind !== 'route' && kind !== 'conversation')) continue;

            const id = `${kind}:${resourceId}`;
            if (seen.has(id)) continue;

            if (kind === 'route') {
                const href = typeof candidate.href === 'string'
                    ? safeWorkspaceHref(candidate.href)
                    : '/';
                if (href === '/') continue;
                if (resourceId === 'apps' && href.includes('/app/view')) continue;
                seen.add(id);
                tabs.push({
                    id: `route:${resourceId}`,
                    kind: 'route',
                    resourceId,
                    title: cleanLabel(candidate.title, formatWorkspaceAppTitle(resourceId)),
                    href,
                });
                continue;
            }

            seen.add(id);
            tabs.push({
                id: `conversation:${resourceId}`,
                kind: 'conversation',
                resourceId,
                title: cleanLabel(candidate.title, 'Untitled conversation'),
                status: null,
            });
        }

        return tabs;
    } catch {
        return [HOME_WORKSPACE_TAB];
    }
}

export function getWorkspaceTabHref(tab: PodWorkspaceTab, podId: string) {
    if (tab.kind === 'home') return `/pod/${podId}`;
    if (tab.kind === 'new') return `/pod/${podId}/conversations/new`;
    if (tab.kind === 'app') {
        return `/pod/${podId}/app/view?page=${encodeURIComponent(tab.resourceId)}`;
    }
    if (tab.kind === 'route') return tab.href;
    return `/pod/${podId}/conversations/${encodeURIComponent(tab.resourceId)}`;
}

function getWorkspaceRouteKey(section: string) {
    switch (section) {
        case 'app':
        case 'apps':
            return 'apps';
        case 'ai':
        case 'agents':
            return 'agents';
        case 'data':
        case 'datastores':
            return 'data';
        case 'docs':
        case 'files':
            return 'files';
        case 'flows':
        case 'workflows':
            return 'workflows';
        case 'kits':
        case 'recipes':
            return 'recipes';
        default:
            return section;
    }
}

export function getActiveWorkspaceTabId(
    podId: string,
    pathname: string,
    appSlug?: string | null,
    searchParams?: URLSearchParams | null,
): string | null {
    if (pathname === `/pod/${podId}` || pathname === `/pod/${podId}/`) return HOME_WORKSPACE_TAB.id;

    // The open app's tab is ephemeral: the strip renders it while the viewer
    // is focused, so it still marks where you are — but it is derived for
    // display in `use-pod-workspace-tabs` and never written into the store,
    // so navigating away takes it with you. Apps live in the sidebar rail,
    // not in the persisted working set.
    if (pathname.startsWith(`/pod/${podId}/app/view`) && appSlug) {
        return `app:${appSlug}`;
    }
    if (pathname.startsWith(`/pod/${podId}/app/view`)) {
        return null;
    }

    const conversationPrefix = `/pod/${podId}/conversations/`;
    if (pathname.startsWith(conversationPrefix)) {
        const encodedId = pathname.slice(conversationPrefix.length).split('/')[0];
        if (!encodedId) return null;
        if (encodedId === 'new') return NEW_WORKSPACE_TAB.id;
        try {
            return `conversation:${decodeURIComponent(encodedId)}`;
        } catch {
            return `conversation:${encodedId}`;
        }
    }

    const base = `/pod/${podId}`;
    if (pathname === `${base}${WIDGET_VIEW_SUFFIX}`) {
        const toolCallId = searchParams?.get('toolCallId')?.trim();
        if (toolCallId) return `route:${widgetWorkspaceRouteKey(toolCallId)}`;
    }

    const section = pathname.slice(base.length).split('/').filter(Boolean)[0];
    if (!section) return HOME_WORKSPACE_TAB.id;
    return `route:${getWorkspaceRouteKey(section)}`;
}

export function getAppSlugFromWorkspaceTab(tab: PodWorkspaceTab): string | null {
    if (tab.kind === 'app') return tab.resourceId;
    if (tab.kind !== 'route' || tab.resourceId !== 'apps') return null;
    try {
        return new URL(tab.href, 'https://lemma.local').searchParams.get('page');
    } catch {
        return null;
    }
}
