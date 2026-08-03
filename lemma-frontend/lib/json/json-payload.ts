// Turning arbitrary data into something renderable. Two callers with the same
// need: chat, which finds JSON inside prose, and the run log, which is handed a
// step's input/output payload directly. Both end up at the same place — a
// pretty-printed, tokenized block — so the formatting and the tokenizer live
// here and neither owns them. Pure string work (no React, no DOM).

export type JsonTokenKind =
    | 'key'
    | 'string'
    | 'number'
    | 'boolean'
    | 'null'
    | 'punctuation'
    | 'plain';

export interface JsonToken {
    text: string;
    kind: JsonTokenKind;
}

export interface JsonPayload {
    /** Source exactly as it appeared, trimmed. */
    raw: string;
    /** Re-serialized with two-space indentation. */
    formatted: string;
    /** Shape summary for the block header: "8 keys", "12 items". */
    summary: string;
    lineCount: number;
}

/**
 * What a value should actually look like on screen. A payload is not always a
 * block: a bare string reads better as prose than as `{"result": "…"}`, and a
 * lone number reads better on the row than in a scroll container.
 */
export type JsonRenderable =
    | { kind: 'text'; text: string }
    | { kind: 'scalar'; text: string }
    | { kind: 'json'; payload: JsonPayload };

/** Past this, reformatting costs more than the readability buys. */
export const MAX_JSON_SOURCE_CHARS = 200_000;
/** Past this, per-token spans cost more than the coloring buys. */
const MAX_TOKENIZE_CHARS = 120_000;

/** Parse a JSON object or array into a renderable payload, or null. Scalars are
 * rejected on purpose: a lone `42` or `"hi"` reads better as plain text. */
export function parseJsonPayload(source: string): JsonPayload | null {
    const parsed = parseJsonContainer(source);
    return parsed ? toPayload(parsed.raw, parsed.value) : null;
}

/**
 * Decide how to render a value that arrived as data rather than as text.
 *
 * Returns null for everything with nothing to say — null, undefined, empty
 * string, `{}`, `[]` — so callers can render nothing without each inventing
 * their own emptiness check.
 *
 * A string holding JSON is unwrapped rather than shown quoted: node outputs
 * routinely carry stringified payloads, and `"{\"count\": 3}"` on screen helps
 * nobody.
 */
export function describeJsonValue(value: unknown): JsonRenderable | null {
    if (value === null || value === undefined) return null;

    if (typeof value === 'string') {
        const trimmed = value.trim();
        if (!trimmed) return null;

        const embedded = parseJsonContainer(trimmed);
        if (embedded && isNonEmptyContainer(embedded.value)) {
            return { kind: 'json', payload: toPayload(embedded.raw, embedded.value) };
        }
        return { kind: 'text', text: value };
    }

    if (typeof value === 'number' || typeof value === 'boolean') {
        return { kind: 'scalar', text: String(value) };
    }

    if (typeof value !== 'object') return null;
    if (!isNonEmptyContainer(value)) return null;

    let formatted: string;
    try {
        formatted = JSON.stringify(value, null, 2);
    } catch {
        // Circular, or a BigInt somewhere in the tree. Say so rather than throw.
        return { kind: 'text', text: String(value) };
    }
    if (typeof formatted !== 'string') return null;

    return { kind: 'json', payload: toPayload(formatted, value as object) };
}

/** True when a value has something worth rendering — used to decide whether a
 * label, a row affordance, or a whole section should exist at all. */
export function hasRenderableJson(value: unknown): boolean {
    return describeJsonValue(value) !== null;
}

/** Split formatted JSON into colorable spans. Token text concatenates back to
 * the input exactly, so nothing is lost when the renderer stitches it together. */
export function tokenizeJson(formatted: string): JsonToken[] {
    if (formatted.length > MAX_TOKENIZE_CHARS) return [{ text: formatted, kind: 'plain' }];

    const tokens: JsonToken[] = [];
    const numberPattern = /-?\d+(?:\.\d+)?(?:[eE][+-]?\d+)?/y;
    const literalPattern = /true|false|null/y;
    let plain = '';
    let index = 0;

    const flushPlain = () => {
        if (plain) tokens.push({ text: plain, kind: 'plain' });
        plain = '';
    };

    while (index < formatted.length) {
        const char = formatted[index];

        if (char === '"') {
            const end = stringEnd(formatted, index);
            flushPlain();
            tokens.push({ text: formatted.slice(index, end), kind: isKeyPosition(formatted, end) ? 'key' : 'string' });
            index = end;
            continue;
        }

        if (char === '-' || (char >= '0' && char <= '9')) {
            numberPattern.lastIndex = index;
            const match = numberPattern.exec(formatted);
            if (match) {
                flushPlain();
                tokens.push({ text: match[0], kind: 'number' });
                index += match[0].length;
                continue;
            }
        }

        if (char === 't' || char === 'f' || char === 'n') {
            literalPattern.lastIndex = index;
            const match = literalPattern.exec(formatted);
            if (match) {
                flushPlain();
                tokens.push({ text: match[0], kind: match[0] === 'null' ? 'null' : 'boolean' });
                index += match[0].length;
                continue;
            }
        }

        if (char === '{' || char === '}' || char === '[' || char === ']' || char === ',' || char === ':') {
            flushPlain();
            tokens.push({ text: char, kind: 'punctuation' });
            index += 1;
            continue;
        }

        plain += char;
        index += 1;
    }

    flushPlain();
    return tokens;
}

export function parseJsonContainer(source: string): { raw: string; value: object } | null {
    const raw = source.trim();
    if (raw.length < 2 || raw.length > MAX_JSON_SOURCE_CHARS) return null;
    if (raw[0] !== '{' && raw[0] !== '[') return null;

    let value: unknown;
    try {
        value = JSON.parse(raw);
    } catch {
        return null;
    }
    if (typeof value !== 'object' || value === null) return null;

    return { raw, value };
}

export function toPayload(raw: string, value: object): JsonPayload {
    const formatted = JSON.stringify(value, null, 2);
    return {
        raw,
        formatted,
        summary: describeJsonShape(value),
        lineCount: formatted.split('\n').length,
    };
}

export function describeJsonShape(value: object): string {
    if (Array.isArray(value)) return value.length === 1 ? '1 item' : `${value.length} items`;
    const keys = Object.keys(value as Record<string, unknown>).length;
    return keys === 1 ? '1 key' : `${keys} keys`;
}

function isNonEmptyContainer(value: object): boolean {
    if (Array.isArray(value)) return value.length > 0;
    return Object.keys(value as Record<string, unknown>).length > 0;
}

function stringEnd(text: string, start: number): number {
    let escaped = false;
    for (let index = start + 1; index < text.length; index += 1) {
        const char = text[index];
        if (escaped) escaped = false;
        else if (char === '\\') escaped = true;
        else if (char === '"') return index + 1;
    }
    return text.length;
}

function isKeyPosition(text: string, afterString: number): boolean {
    for (let index = afterString; index < text.length; index += 1) {
        const char = text[index];
        if (char === ' ' || char === '\t') continue;
        return char === ':';
    }
    return false;
}
