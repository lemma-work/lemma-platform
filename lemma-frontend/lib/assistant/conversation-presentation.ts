export const CONVERSATION_PRESENTED_RESOURCE_PARAM = 'presented';
export const CONVERSATION_STAGE_EMBED_PARAM = 'embed';
export const CONVERSATION_STAGE_EMBED_VALUE = 'conversation-stage';
export const CONVERSATION_STAGE_NAVIGATION_MESSAGE_TYPE = 'lemma:conversation-stage:navigate';

const ASSISTANT_CONVERSATION_PARAM = 'assistantConversationId';
const WIDGET_VIEW_SUFFIX = '/widgets/view';

function localResourceUrl(href: string): URL | null {
    if (!href || !href.startsWith('/')) return null;

    try {
        const url = new URL(href, 'https://lemma.local');
        return url.origin === 'https://lemma.local' ? url : null;
    } catch {
        return null;
    }
}

function hrefFromLocalUrl(url: URL): string {
    return `${url.pathname}${url.search}${url.hash}`;
}

export function buildConversationStageNavigationMessage(href: string) {
    return {
        type: CONVERSATION_STAGE_NAVIGATION_MESSAGE_TYPE,
        href,
    } as const;
}

export function requestConversationStageNavigation(href: string): boolean {
    if (typeof window === 'undefined' || window.self === window.top) return false;

    const params = new URLSearchParams(window.location.search);
    if (params.get(CONVERSATION_STAGE_EMBED_PARAM) !== CONVERSATION_STAGE_EMBED_VALUE) {
        return false;
    }

    window.parent.postMessage(
        buildConversationStageNavigationMessage(href),
        window.location.origin,
    );
    return true;
}

export function resolveConversationStageNavigationHref(
    value: unknown,
    podId: string,
): string | null {
    if (!value || typeof value !== 'object') return null;

    const message = value as { type?: unknown; href?: unknown };
    if (message.type !== CONVERSATION_STAGE_NAVIGATION_MESSAGE_TYPE) return null;
    if (typeof message.href !== 'string') return null;

    const url = localResourceUrl(message.href);
    if (!url) return null;

    const conversationBase = `/pod/${encodeURIComponent(podId)}/conversations`;
    if (url.pathname !== conversationBase && !url.pathname.startsWith(`${conversationBase}/`)) {
        return null;
    }

    url.searchParams.delete(CONVERSATION_STAGE_EMBED_PARAM);
    url.searchParams.delete('assistant');
    url.searchParams.delete('presentation');
    url.searchParams.delete(CONVERSATION_PRESENTED_RESOURCE_PARAM);
    return hrefFromLocalUrl(url);
}

export function normalizeConversationPresentedResourceHref(
    value: string | null | undefined,
    podId: string,
): string | null {
    if (!value) return null;
    const url = localResourceUrl(value);
    if (!url) return null;

    const podBase = `/pod/${encodeURIComponent(podId)}`;
    if (!url.pathname.startsWith(`${podBase}/`)) return null;
    if (url.pathname.startsWith(`${podBase}/conversations`)) return null;

    return hrefFromLocalUrl(url);
}

export function buildConversationPresentationHref({
    pathname,
    searchParams,
    resourceHref,
    activeConversationId,
}: {
    pathname: string;
    searchParams: string;
    resourceHref: string;
    activeConversationId?: string | null;
}): string | null {
    const match = pathname.match(/^\/pod\/([^/]+)\/conversations\/([^/]+)$/);
    if (!match) return null;

    const [podSegment, routeConversationSegment] = [match[1], match[2]];
    const targetConversationSegment = routeConversationSegment === 'new' && activeConversationId
        ? encodeURIComponent(activeConversationId)
        : routeConversationSegment;
    const params = new URLSearchParams(searchParams);
    params.delete(ASSISTANT_CONVERSATION_PARAM);
    params.set(CONVERSATION_PRESENTED_RESOURCE_PARAM, resourceHref);

    return `/pod/${podSegment}/conversations/${targetConversationSegment}?${params.toString()}`;
}

function prepareWidgetConversationContext(url: URL) {
    if (!url.pathname.endsWith(WIDGET_VIEW_SUFFIX)) return;
    const conversationId = url.searchParams.get(ASSISTANT_CONVERSATION_PARAM);
    if (conversationId && !url.searchParams.has('conversationId')) {
        url.searchParams.set('conversationId', conversationId);
    }
}

export function buildConversationStageEmbedHref(resourceHref: string): string | null {
    const url = localResourceUrl(resourceHref);
    if (!url) return null;

    prepareWidgetConversationContext(url);
    url.searchParams.delete(ASSISTANT_CONVERSATION_PARAM);
    url.searchParams.delete('assistant');
    url.searchParams.delete('presentation');
    url.searchParams.set(CONVERSATION_STAGE_EMBED_PARAM, CONVERSATION_STAGE_EMBED_VALUE);
    return hrefFromLocalUrl(url);
}

export function buildConversationStandaloneResourceHref(resourceHref: string): string | null {
    const url = localResourceUrl(resourceHref);
    if (!url) return null;

    prepareWidgetConversationContext(url);
    url.searchParams.delete(ASSISTANT_CONVERSATION_PARAM);
    url.searchParams.delete(CONVERSATION_STAGE_EMBED_PARAM);
    url.searchParams.delete('assistant');
    url.searchParams.delete('presentation');
    if (url.pathname.endsWith(WIDGET_VIEW_SUFFIX)) {
        url.searchParams.set('standalone', '1');
    }
    return hrefFromLocalUrl(url);
}

export function buildResourceShareUrl(resourceHref: string, origin: string): string | null {
    const standaloneHref = buildConversationStandaloneResourceHref(resourceHref);
    if (!standaloneHref) return null;

    try {
        return new URL(standaloneHref, origin).toString();
    } catch {
        return null;
    }
}

export function removeConversationPresentationParam(searchParams: string): string {
    const params = new URLSearchParams(searchParams);
    params.delete(CONVERSATION_PRESENTED_RESOURCE_PARAM);
    return params.toString();
}
