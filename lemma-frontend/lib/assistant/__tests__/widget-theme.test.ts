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

    it('sends the chart ramp, not brand hues with a status colour standing in', () => {
        // `--state-success` as "series 3" steals a meaning — a green bar that
        // encodes nothing still reads as "good" — and leaves the categorical set
        // without a step chosen to separate from its neighbours.
        const values: Record<string, string> = {
            '--chart-1': '#795bce',
            '--chart-3': '#b95400',
            '--state-success': '#1f9254',
            '--font-mono': 'IBM Plex Mono, monospace',
            '--shadow-md': '0 4px 14px rgb(0 0 0 / 0.05)',
        };
        const message = buildWidgetThemeMessage({
            theme: 'light',
            readToken: (name) => values[name] || '',
            fontFamily: 'Inter, sans-serif',
        });

        expect(message.tokens['--lemma-widget-chart-1']).toBe('#795bce');
        expect(message.tokens['--lemma-widget-chart-3']).toBe('#b95400');
        expect(message.tokens['--lemma-widget-chart-3']).not.toBe(values['--state-success']);
        expect(message.tokens['--lemma-widget-font-mono']).toBe('IBM Plex Mono, monospace');
        expect(message.tokens['--lemma-widget-shadow-rest']).toBe('0 4px 14px rgb(0 0 0 / 0.05)');
        // This frontend has no token for the ink that goes on a fill, and a
        // guessed one is worse than none.
        expect(message.tokens['--lemma-widget-on-accent']).toBeUndefined();
    });
});
