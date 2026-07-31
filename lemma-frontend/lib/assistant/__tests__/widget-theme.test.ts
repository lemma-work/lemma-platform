import { describe, expect, it } from 'vitest';

import {
    buildWidgetThemeMessage,
    resolveWidgetTheme,
} from '../widget-theme';

describe('resolveWidgetTheme', () => {
    it('prefers the explicit Lemma theme over the system preference', () => {
        expect(resolveWidgetTheme('light', true)).toBe('light');
        expect(resolveWidgetTheme('dark', false)).toBe('dark');
    });

    it('falls back to the operating-system preference', () => {
        expect(resolveWidgetTheme(undefined, true)).toBe('dark');
        expect(resolveWidgetTheme('system', false)).toBe('light');
    });
});

describe('buildWidgetThemeMessage', () => {
    it('maps the stable platform subset into public widget tokens', () => {
        const values: Record<string, string> = {
            '--pod-main-bg': '#fff',
            '--surface-1': '#fafafa',
            '--text-primary': '#111',
            '--interactive-primary': '#5f61d8',
            '--radius-lg': '10px',
        };
        const message = buildWidgetThemeMessage({
            theme: 'light',
            readToken: (name) => values[name] || '',
            fontFamily: 'Inter, sans-serif',
        });

        expect(message.type).toBe('lemma-widget-theme');
        expect(message.theme).toBe('light');
        expect(message.tokens).toMatchObject({
            '--lemma-widget-bg': '#fff',
            '--lemma-widget-surface': '#fafafa',
            '--lemma-widget-text': '#111',
            '--lemma-widget-accent': '#5f61d8',
            '--lemma-widget-radius': '10px',
            '--lemma-widget-font': 'Inter, sans-serif',
            '--lemma-widget-color-scheme': 'light',
            '--lemma-widget-danger-soft': '#fef2f2',
        });
        expect(message.tokens['--lemma-widget-muted']).toBeUndefined();
    });
});
