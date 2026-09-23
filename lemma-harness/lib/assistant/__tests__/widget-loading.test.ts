import { describe, expect, it } from 'vitest';

import {
    isWidgetLoading,
    normalizeWidgetLoadingMessages,
    selectWidgetLoadingMessage,
} from '../widget-loading';

describe('isWidgetLoading', () => {
    it('stays active while the embed token is loading', () => {
        expect(isWidgetLoading({
            embedTokenLoading: true,
            iframeSrc: null,
            loadedIframeSrc: null,
        })).toBe(true);
    });

    it('stays active until the iframe source has loaded', () => {
        expect(isWidgetLoading({
            embedTokenLoading: false,
            iframeSrc: 'https://api.example.test/widget',
            loadedIframeSrc: null,
        })).toBe(true);
        expect(isWidgetLoading({
            embedTokenLoading: false,
            iframeSrc: 'https://api.example.test/widget',
            loadedIframeSrc: 'https://api.example.test/widget',
        })).toBe(false);
    });

    it('treats external iframe sources the same as content widget sources', () => {
        expect(isWidgetLoading({
            embedTokenLoading: false,
            iframeSrc: 'https://widgets.example.test/board',
            loadedIframeSrc: null,
        })).toBe(true);
    });

    it('returns to loading when the iframe source changes', () => {
        expect(isWidgetLoading({
            embedTokenLoading: false,
            iframeSrc: 'https://api.example.test/widget?token=new',
            loadedIframeSrc: 'https://api.example.test/widget?token=old',
        })).toBe(true);
    });
});

describe('widget loading messages', () => {
    it('normalizes and limits messages', () => {
        expect(normalizeWidgetLoadingMessages([
            ' First ',
            '',
            'Second',
            'Third',
            'Fourth',
            'Fifth',
        ])).toEqual(['First', 'Second', 'Third', 'Fourth']);
    });

    it('rotates messages and falls back when none are configured', () => {
        expect(selectWidgetLoadingMessage(['First', 'Second'], 0)).toBe('First');
        expect(selectWidgetLoadingMessage(['First', 'Second'], 3)).toBe('Second');
        expect(selectWidgetLoadingMessage([], 8)).toBe('Loading widget');
    });
});
