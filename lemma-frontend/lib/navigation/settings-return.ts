export function normalizeInternalReturnPath(value: string | null | undefined): string | null {
    const candidate = value?.trim();
    if (!candidate || !candidate.startsWith('/') || candidate.startsWith('//')) return null;

    try {
        const parsed = new URL(candidate, 'https://lemma.local');
        if (parsed.origin !== 'https://lemma.local') return null;
        return `${parsed.pathname}${parsed.search}${parsed.hash}`;
    } catch {
        return null;
    }
}

export function withSettingsReturnPath(settingsHref: string, returnPath: string): string {
    const [pathWithQuery, hash = ''] = settingsHref.split('#', 2);
    const [pathname, query = ''] = pathWithQuery.split('?', 2);
    const params = new URLSearchParams(query);
    params.set('returnTo', returnPath);
    const nextQuery = params.toString();
    return `${pathname}${nextQuery ? `?${nextQuery}` : ''}${hash ? `#${hash}` : ''}`;
}
