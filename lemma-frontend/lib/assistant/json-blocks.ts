// JSON detection for chat messages. Agents answer with JSON constantly — fenced
// as ```json, fenced with no language, or dumped straight into the prose — and
// markdown renders the last two as an unreadable wall of run-together text.
// These helpers find the JSON, leave the prose around it untouched, and hand the
// renderer a pretty-printed, tokenized payload. Pure string work (no React, no
// DOM) so the detection rules stay unit-testable.

export type AssistantJsonTokenKind =
    | 'key'
    | 'string'
    | 'number'
    | 'boolean'
    | 'null'
    | 'punctuation'
    | 'plain';

export interface AssistantJsonToken {
    text: string;
    kind: AssistantJsonTokenKind;
}

export interface AssistantJsonPayload {
    /** Source exactly as it appeared in the message, trimmed. */
    raw: string;
    /** Re-serialized with two-space indentation. */
    formatted: string;
    /** Shape summary for the block header: "8 keys", "12 items". */
    summary: string;
    lineCount: number;
}

export type AssistantMessageSegment =
    | { kind: 'markdown'; text: string }
    | { kind: 'json'; json: AssistantJsonPayload };

/** Past this, reformatting costs more than the readability buys. */
const MAX_JSON_SOURCE_CHARS = 200_000;
/** Past this, per-token spans cost more than the coloring buys. */
const MAX_TOKENIZE_CHARS = 120_000;

const JSON_FENCE_LANGUAGES = new Set(['json', 'jsonc', 'json5', 'geojson', 'jsonld']);

/** Parse a JSON object or array into a renderable payload, or null. Scalars are
 * rejected on purpose: a lone `42` or `"hi"` reads better as plain text. */
export function parseAssistantJson(source: string): AssistantJsonPayload | null {
    const parsed = parseJsonContainer(source);
    return parsed ? toPayload(parsed.raw, parsed.value) : null;
}

/** True for fence infostrings we treat as JSON (```json, ```jsonc, …). */
export function isJsonFenceLanguage(language: string | null | undefined): boolean {
    return typeof language === 'string' && JSON_FENCE_LANGUAGES.has(language.toLowerCase());
}

/** Pull the language out of a highlight class such as `language-json`. */
export function fenceLanguageFromClassName(className: unknown): string | null {
    const names = Array.isArray(className)
        ? className.filter((entry): entry is string => typeof entry === 'string').join(' ')
        : typeof className === 'string'
            ? className
            : '';
    const match = /(?:^|\s)language-([\w+#-]+)/.exec(names);
    return match ? match[1].toLowerCase() : null;
}

interface HastNodeLike {
    type?: string;
    tagName?: string;
    value?: string;
    properties?: { className?: unknown };
    children?: HastNodeLike[];
}

/** Read the fenced code out of react-markdown's `<pre>` hast node. The rendered
 * children are React elements by then, so the node is the only place the raw
 * source and the infostring both survive. */
export function fencedCodeFromPreNode(node: unknown): { language: string | null; text: string } | null {
    const pre = node as HastNodeLike | undefined;
    if (!pre || pre.tagName !== 'pre' || !Array.isArray(pre.children)) return null;

    const code = pre.children.find((child) => child?.tagName === 'code');
    if (!code || !Array.isArray(code.children)) return null;

    const text = code.children
        .map((child) => (typeof child.value === 'string' ? child.value : ''))
        .join('');
    if (!text) return null;

    return { language: fenceLanguageFromClassName(code.properties?.className), text };
}

/** Split a message into prose and bare-JSON runs. Fenced code is passed through
 * untouched — the markdown renderer handles those through `fencedCodeFromPreNode`. */
export function splitAssistantMessageSegments(content: string): AssistantMessageSegment[] {
    if (!content.includes('{') && !content.includes('[')) {
        return content.trim() ? [{ kind: 'markdown', text: content }] : [];
    }

    const segments: AssistantMessageSegment[] = [];
    let pending = '';
    let cursor = 0;
    let openFence: string | null = null;

    const flushMarkdown = () => {
        if (pending.trim()) segments.push({ kind: 'markdown', text: pending });
        pending = '';
    };

    while (cursor < content.length) {
        const newline = content.indexOf('\n', cursor);
        const lineEnd = newline === -1 ? content.length : newline;
        const line = content.slice(cursor, lineEnd);
        const fence = fenceMarkerOf(line);

        if (openFence) {
            if (fence === openFence) openFence = null;
        } else if (fence) {
            openFence = fence;
        } else {
            const offset = bareJsonCandidateOffset(line);
            const start = offset === null ? -1 : cursor + offset;
            const end = start === -1 ? null : balancedSpanEnd(content, start);
            const parsed = end === null ? null : parseJsonContainer(content.slice(start, end));

            if (parsed && start !== -1 && end !== null && isWorthwhileBareJson(parsed.value)) {
                pending += content.slice(cursor, start);
                flushMarkdown();
                segments.push({ kind: 'json', json: toPayload(parsed.raw, parsed.value) });
                cursor = end;
                continue;
            }
        }

        pending += content.slice(cursor, lineEnd) + (newline === -1 ? '' : '\n');
        cursor = lineEnd + 1;
    }

    flushMarkdown();
    return segments;
}

/** Split formatted JSON into colorable spans. Token text concatenates back to
 * the input exactly, so nothing is lost when the renderer stitches it together. */
export function tokenizeAssistantJson(formatted: string): AssistantJsonToken[] {
    if (formatted.length > MAX_TOKENIZE_CHARS) return [{ text: formatted, kind: 'plain' }];

    const tokens: AssistantJsonToken[] = [];
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

function parseJsonContainer(source: string): { raw: string; value: object } | null {
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

function toPayload(raw: string, value: object): AssistantJsonPayload {
    const formatted = JSON.stringify(value, null, 2);
    return {
        raw,
        formatted,
        summary: describeJsonShape(value),
        lineCount: formatted.split('\n').length,
    };
}

function describeJsonShape(value: object): string {
    if (Array.isArray(value)) return value.length === 1 ? '1 item' : `${value.length} items`;
    const keys = Object.keys(value as Record<string, unknown>).length;
    return keys === 1 ? '1 key' : `${keys} keys`;
}

/** Unfenced JSON has to earn its block. `[1]` is a footnote, `[a](b)` is a link,
 * and `["red", "blue"]` is prose — so objects need a key and arrays have to look
 * like a record list. */
function isWorthwhileBareJson(value: object): boolean {
    if (Array.isArray(value)) {
        return value.length > 0 && value.every((item) => typeof item === 'object' && item !== null);
    }
    return Object.keys(value as Record<string, unknown>).length > 0;
}

/** Where a bare JSON run could start on this line: at the margin, or right after
 * a plain `label:` lead-in. Anything else (list bullets, table pipes, inline
 * code) stays prose. */
function bareJsonCandidateOffset(line: string): number | null {
    const atMargin = /^( {0,3})(?=[[{])/.exec(line);
    if (atMargin) return atMargin[1].length;

    const labelled = /^ {0,3}[A-Za-z][^`{}[\]\n]{0,80}[:=][ \t]*(?=[[{])/.exec(line);
    return labelled ? labelled[0].length : null;
}

/** End (exclusive) of the balanced `{…}` / `[…]` run starting at `start`, or null
 * when it never closes. String-aware so braces inside values do not confuse it. */
function balancedSpanEnd(content: string, start: number): number | null {
    const closer = content[start] === '{' ? '}' : ']';
    let depth = 0;
    let inString = false;
    let escaped = false;

    for (let index = start; index < content.length; index += 1) {
        if (index - start > MAX_JSON_SOURCE_CHARS) return null;
        const char = content[index];

        if (inString) {
            if (escaped) escaped = false;
            else if (char === '\\') escaped = true;
            else if (char === '"') inString = false;
            continue;
        }

        if (char === '"') {
            inString = true;
        } else if (char === '{' || char === '[') {
            depth += 1;
        } else if (char === '}' || char === ']') {
            depth -= 1;
            if (depth === 0) return char === closer ? index + 1 : null;
            if (depth < 0) return null;
        }
    }

    return null;
}

function fenceMarkerOf(line: string): string | null {
    const match = /^ {0,3}(`{3,}|~{3,})/.exec(line);
    return match ? match[1][0] : null;
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
