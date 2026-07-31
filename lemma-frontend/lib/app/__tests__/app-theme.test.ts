import { describe, expect, it } from 'vitest';

import { buildAppThemeMessage } from '../app-theme';

describe('buildAppThemeMessage', () => {
    it('maps the stable host contract without leaking internal token names', () => {
        const values: Record<string, string> = {
            '--pod-main-bg': '#fcfcfb',
            '--surface-1': '#fff',
            '--text-primary': '#181816',
            '--text-on-brand': '#fff',
            '--interactive-primary': '#5f61d8',
            '--radius-lg': '8px',
            '--dur-panel': '260ms',
            '--brand-coral': '#f06b3e',
        };

        const message = buildAppThemeMessage({
            theme: 'dark',
            readToken: (name) => values[name] || '',
            fontFamily: 'IBM Plex Sans, sans-serif',
        });

        expect(message).toMatchObject({
            type: 'lemma-app-theme',
            theme: 'dark',
            density: 'compact',
            tokens: {
                '--lemma-app-bg': '#fcfcfb',
                '--lemma-app-surface': '#fff',
                '--lemma-app-text': '#181816',
                '--lemma-app-text-on-accent': '#fff',
                '--lemma-app-accent': '#5f61d8',
                '--lemma-app-radius-lg': '8px',
                '--lemma-app-duration-panel': '260ms',
                '--lemma-app-chart-1': '#f06b3e',
                '--lemma-app-font': 'IBM Plex Sans, sans-serif',
                '--lemma-app-color-scheme': 'dark',
            },
        });
        expect(message.tokens['--pod-main-bg']).toBeUndefined();
    });
});
