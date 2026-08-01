import { describe, it, expect } from 'vitest';

import {
    fenceLanguageFromClassName,
    fencedCodeFromPreNode,
    isJsonFenceLanguage,
    parseAssistantJson,
    splitAssistantMessageSegments,
    tokenizeAssistantJson,
} from '../json-blocks';

describe('parseAssistantJson', () => {
    it('formats and summarizes an object', () => {
        const payload = parseAssistantJson('{"name":"lemma","count":2}');
        expect(payload?.formatted).toBe('{\n  "name": "lemma",\n  "count": 2\n}');
        expect(payload?.summary).toBe('2 keys');
        expect(payload?.lineCount).toBe(4);
    });

    it('summarizes arrays by item count, singular included', () => {
        expect(parseAssistantJson('[{"a":1}]')?.summary).toBe('1 item');
        expect(parseAssistantJson('[1,2,3]')?.summary).toBe('3 items');
        expect(parseAssistantJson('{"a":1}')?.summary).toBe('1 key');
    });

    it('rejects scalars, invalid JSON, and prose', () => {
        expect(parseAssistantJson('42')).toBeNull();
        expect(parseAssistantJson('"hello"')).toBeNull();
        expect(parseAssistantJson('null')).toBeNull();
        expect(parseAssistantJson('{name: "lemma"}')).toBeNull();
        expect(parseAssistantJson('not json at all')).toBeNull();
    });
});

describe('isJsonFenceLanguage', () => {
    it('accepts json infostring variants only', () => {
        expect(isJsonFenceLanguage('json')).toBe(true);
        expect(isJsonFenceLanguage('JSON')).toBe(true);
        expect(isJsonFenceLanguage('jsonc')).toBe(true);
        expect(isJsonFenceLanguage('python')).toBe(false);
        expect(isJsonFenceLanguage(null)).toBe(false);
    });
});

describe('fenceLanguageFromClassName', () => {
    it('reads the language from string and array class names', () => {
        expect(fenceLanguageFromClassName('language-json')).toBe('json');
        expect(fenceLanguageFromClassName(['hljs', 'language-JSON'])).toBe('json');
        expect(fenceLanguageFromClassName('hljs')).toBeNull();
        expect(fenceLanguageFromClassName(undefined)).toBeNull();
    });
});

describe('fencedCodeFromPreNode', () => {
    const preNode = (className: string[] | undefined, value: string) => ({
        type: 'element',
        tagName: 'pre',
        children: [
            {
                type: 'element',
                tagName: 'code',
                properties: className ? { className } : {},
                children: [{ type: 'text', value }],
            },
        ],
    });

    it('extracts the language and raw source', () => {
        expect(fencedCodeFromPreNode(preNode(['language-json'], '{"a":1}'))).toEqual({
            language: 'json',
            text: '{"a":1}',
        });
    });

    it('reports a null language for untagged fences', () => {
        expect(fencedCodeFromPreNode(preNode(undefined, '{"a":1}'))?.language).toBeNull();
    });

    it('ignores anything that is not a pre wrapping a code element', () => {
        expect(fencedCodeFromPreNode(undefined)).toBeNull();
        expect(fencedCodeFromPreNode({ tagName: 'div', children: [] })).toBeNull();
        expect(fencedCodeFromPreNode({ tagName: 'pre', children: [{ tagName: 'span', children: [] }] })).toBeNull();
    });
});

describe('splitAssistantMessageSegments', () => {
    it('leaves plain prose as a single markdown segment', () => {
        const segments = splitAssistantMessageSegments('Here is a plain answer.');
        expect(segments).toEqual([{ kind: 'markdown', text: 'Here is a plain answer.' }]);
    });

    it('lifts a bare JSON payload out of the surrounding prose', () => {
        const segments = splitAssistantMessageSegments(
            'Here is the record:\n{"id": 7, "name": "lemma"}\nLet me know if that works.',
        );
        expect(segments.map((segment) => segment.kind)).toEqual(['markdown', 'json', 'markdown']);
        expect(segments[0]).toEqual({ kind: 'markdown', text: 'Here is the record:\n' });
        expect(segments[1].kind === 'json' && segments[1].json.summary).toBe('2 keys');
        expect(segments[2]).toEqual({ kind: 'markdown', text: '\nLet me know if that works.' });
    });

    it('handles a message that is nothing but JSON', () => {
        const segments = splitAssistantMessageSegments('{\n  "ok": true\n}');
        expect(segments).toHaveLength(1);
        expect(segments[0].kind).toBe('json');
    });

    it('keeps a same-line label as prose and detects the JSON after it', () => {
        const segments = splitAssistantMessageSegments('Result: {"status": "done"}');
        expect(segments[0]).toEqual({ kind: 'markdown', text: 'Result: ' });
        expect(segments[1].kind).toBe('json');
    });

    it('detects several payloads in one message', () => {
        const segments = splitAssistantMessageSegments('{"a": 1}\n\nand\n\n[{"b": 2}]');
        expect(segments.map((segment) => segment.kind)).toEqual(['json', 'markdown', 'json']);
    });

    it('leaves fenced code alone for the markdown renderer', () => {
        const content = 'Config:\n\n```json\n{"a": 1}\n```\n\nDone.';
        expect(splitAssistantMessageSegments(content)).toEqual([{ kind: 'markdown', text: content }]);
    });

    it('leaves tilde fences alone too', () => {
        const content = '~~~\n{"a": 1}\n~~~';
        expect(splitAssistantMessageSegments(content)).toEqual([{ kind: 'markdown', text: content }]);
    });

    it('does not mistake markdown syntax for JSON', () => {
        const cases = [
            '[the docs](https://lemma.test/docs) explain it',
            '[1] is a footnote marker',
            'Use `{"a": 1}` when calling it',
            '- item: {"a": 1}',
            '| a | b |\n| --- | --- |',
            'Template {placeholder} stays put',
            '["red", "blue"] are the theme colors',
        ];
        for (const content of cases) {
            expect(splitAssistantMessageSegments(content)).toEqual([{ kind: 'markdown', text: content }]);
        }
    });

    it('leaves unbalanced or partial JSON as prose, so streaming never flickers a broken block', () => {
        const partial = '{"name": "lem';
        expect(splitAssistantMessageSegments(partial)).toEqual([{ kind: 'markdown', text: partial }]);
    });

    it('drops empty markdown around a payload rather than emitting blank segments', () => {
        const segments = splitAssistantMessageSegments('\n\n{"a": 1}\n\n');
        expect(segments).toHaveLength(1);
        expect(segments[0].kind).toBe('json');
    });

    it('is not confused by braces inside string values', () => {
        const segments = splitAssistantMessageSegments('{"template": "{ not a brace }", "escaped": "quote \\" here"}');
        expect(segments).toHaveLength(1);
        expect(segments[0].kind === 'json' && segments[0].json.summary).toBe('2 keys');
    });
});

describe('tokenizeAssistantJson', () => {
    it('round-trips the formatted source exactly', () => {
        const formatted = parseAssistantJson('{"a":[1,2],"b":{"c":null}}')!.formatted;
        expect(tokenizeAssistantJson(formatted).map((token) => token.text).join('')).toBe(formatted);
    });

    it('separates keys from string values', () => {
        const tokens = tokenizeAssistantJson('{\n  "name": "lemma"\n}');
        expect(tokens.find((token) => token.text === '"name"')?.kind).toBe('key');
        expect(tokens.find((token) => token.text === '"lemma"')?.kind).toBe('string');
    });

    it('classifies numbers, booleans, null, and punctuation', () => {
        const tokens = tokenizeAssistantJson('{\n  "n": -1.5e3,\n  "t": true,\n  "z": null\n}');
        const kindOf = (text: string) => tokens.find((token) => token.text === text)?.kind;
        expect(kindOf('-1.5e3')).toBe('number');
        expect(kindOf('true')).toBe('boolean');
        expect(kindOf('null')).toBe('null');
        expect(kindOf('{')).toBe('punctuation');
        expect(kindOf(':')).toBe('punctuation');
    });

    it('does not treat literal-looking text inside strings as literals', () => {
        const tokens = tokenizeAssistantJson('{\n  "a": "true null 12"\n}');
        expect(tokens.find((token) => token.text.includes('true null 12'))?.kind).toBe('string');
    });
});
