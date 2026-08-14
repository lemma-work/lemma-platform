/**
 * "ledflex-support" → "Ledflex support". "deepseek-v4-flash" → "Deepseek v4 flash".
 *
 * Resources are named as slugs because that is what a pod bundle, a URL and an
 * API call all need. Printing that slug back at a person is a leak: the name
 * they read should be a name, not an identifier.
 *
 * Sentence case, not title case. `docs/design-tokens.md` is explicit that
 * product copy is sentence case and uppercase is reserved for mono eyebrows and
 * operational labels — this used to title-case every word, so a pod full of
 * resources read as a page of Proper Nouns.
 *
 * Display only. Never use the result for hrefs, API calls, or anywhere else the
 * raw name is the key — and never for matching, since it is lossy.
 */

/**
 * Words that are shouted rather than spoken. Without these, sentence case
 * turns `gpt-4o` into "Gpt 4o", which is not humanising a name so much as
 * misspelling it.
 */
const ACRONYMS = new Set(['ai', 'api', 'gpt', 'llm', 'sql', 'csv', 'pdf', 'url', 'id', 'ui']);

export function humanizeName(name: string): string {
    const words = name
        .replace(/[_-]+/g, ' ')
        .replace(/\s+/g, ' ')
        .trim();

    if (!words) return '';

    return words
        .split(' ')
        .map((word, index) => {
            if (ACRONYMS.has(word.toLowerCase())) return word.toUpperCase();
            // Only the first word is lifted; the rest keep whatever case they
            // arrived with, so "v4" stays "v4" and an already-capitalised
            // proper noun in the middle is not flattened.
            if (index !== 0) return word;
            return word.charAt(0).toUpperCase() + word.slice(1);
        })
        .join(' ');
}
