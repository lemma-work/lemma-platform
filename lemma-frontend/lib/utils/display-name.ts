/**
 * "ledflex-support" → "Ledflex Support".
 *
 * Resources are named as slugs because that is what a pod bundle, a URL and an
 * API call all need. Printing that slug back at a person is a leak: the name
 * they read should be a name, not an identifier.
 *
 * Display only. Never use the result for hrefs, API calls, or anywhere else the
 * raw name is the key.
 */
export function formatSlugTitle(name: string): string {
    return name
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim()
        .split(' ')
        .map((word) => word.charAt(0).toUpperCase() + word.slice(1))
        .join(' ');
}
