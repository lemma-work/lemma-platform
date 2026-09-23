// JSON detection for chat messages. Agents answer with JSON constantly — fenced
// as ```json, fenced with no language, or dumped straight into the prose — and
// markdown renders the last two as an unreadable wall of run-together text.
// These helpers find the JSON in a message and leave the prose around it
// untouched. Pure string work (no React, no DOM) so the detection rules stay
// unit-testable.
//
// Formatting and tokenizing the JSON once found is not chat-specific — the run
// log renders the same blocks from step payloads — so that half lives in
// lib/json/json-payload and is re-exported here under the chat-facing names.

import {
    MAX_JSON_SOURCE_CHARS,
    parseJsonContainer,
    parseJsonPayload,
    toPayload,
    tokenizeJson,
    type JsonPayload,
    type JsonToken,
    type JsonTokenKind,
} from '@/lib/json/json-payload';

export type AssistantJsonTokenKind = JsonTokenKind;
export type AssistantJsonToken = JsonToken;
export type AssistantJsonPayload = JsonPayload;

export type AssistantMessageSegment =
    | { kind: 'markdown'; text: string }
    | { kind: 'json'; json: AssistantJsonPayload };

const JSON_FENCE_LANGUAGES = new Set(['json', 'jsonc', 'json5', 'geojson', 'jsonld']);

/** Parse a JSON object or array into a renderable payload, or null. Scalars are
 * rejected on purpose: a lone `42` or `"hi"` reads better as plain text. */
export const parseAssistantJson = parseJsonPayload;

/** Split formatted JSON into colorable spans. Token text concatenates back to
 * the input exactly, so nothing is lost when the renderer stitches it together. */
export const tokenizeAssistantJson = tokenizeJson;

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
