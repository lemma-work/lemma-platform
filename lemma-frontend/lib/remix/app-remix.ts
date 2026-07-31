const MAX_SOURCE_URL_LENGTH = 2_048;

export function normalizeRemixSource(value: string | null | undefined): string | null {
    const candidate = value?.trim();
    if (!candidate || candidate.length > MAX_SOURCE_URL_LENGTH) return null;

    try {
        const url = new URL(candidate);
        if (url.protocol !== 'http:' && url.protocol !== 'https:') return null;
        return url.toString();
    } catch {
        return null;
    }
}

export function remixSourceLabel(source: string): string {
    const url = new URL(source);
    return url.hostname.replace(/^www\./, '');
}

export function buildAppRemixPrompt(source: string): string {
    return [
        `Remix this app in this pod: ${source}`,
        '',
        'Inspect the app, understand the useful experience, and rebuild or adapt it for me here. Start by telling me what you can observe, then propose the first concrete version and build it with me.',
    ].join('\n');
}

export function buildAppRemixConversationHref(podId: string, source: string): string {
    const params = new URLSearchParams({
        assistantMessage: buildAppRemixPrompt(source),
        conversationInstructions: [
            'This conversation started from the Remix on Lemma badge on a hosted app.',
            'Treat the source app as inspiration and user-authorized context. Inspect only what is available with the current credentials. Rebuild or adapt the experience in this destination pod; do not claim that private data, integrations, workflows, or hidden implementation details were copied when they were not observable.',
        ].join('\n\n'),
        conversationMetadata: JSON.stringify({
            source: 'public_app_remix',
            source_url: source,
        }),
    });

    return `/pod/${encodeURIComponent(podId)}/conversations/new?${params.toString()}`;
}

export function buildCreatePodForRemixHref(source: string): string {
    return `/create-pod?${new URLSearchParams({ remixSource: source }).toString()}`;
}
