// Per-app accent so a gallery of app cards reads as distinct tiles instead of
// grey sameness. Maps a stable key (slug) to one of the pod's semantic accent
// tones — see `.app-tile[data-accent]` in styles/features/resource-ledgers.css.

export const APP_ACCENTS = [
    'brand',
    'intelligence',
    'collaboration',
    'info',
    'success',
    'delight',
] as const;

export type AppAccent = (typeof APP_ACCENTS)[number];

export function getAppAccent(key: string | null | undefined): AppAccent {
    const source = (key || '').trim();
    if (!source) return APP_ACCENTS[0];
    let hash = 0;
    for (let index = 0; index < source.length; index += 1) {
        hash = (hash * 31 + source.charCodeAt(index)) >>> 0;
    }
    return APP_ACCENTS[hash % APP_ACCENTS.length];
}
