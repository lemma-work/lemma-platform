export const WIDGET_THEME_MESSAGE_TYPE = 'lemma-widget-theme';

const WIDGET_THEME_TOKEN_SOURCES = {
    '--lemma-widget-bg': '--pod-main-bg',
    '--lemma-widget-surface': '--surface-1',
    '--lemma-widget-subtle': '--surface-2',
    '--lemma-widget-text': '--text-primary',
    '--lemma-widget-muted': '--text-secondary',
    '--lemma-widget-faint': '--text-tertiary',
    '--lemma-widget-border': '--border-subtle',
    '--lemma-widget-border-strong': '--border-default',
    '--lemma-widget-accent': '--interactive-primary',
    '--lemma-widget-accent-hover': '--action-primary-hover',
    '--lemma-widget-accent-soft': '--action-primary-soft',
    '--lemma-widget-success': '--state-success',
    '--lemma-widget-warning': '--state-warning',
    '--lemma-widget-danger': '--state-error',
    '--lemma-widget-info': '--state-info',
    '--lemma-widget-radius-sm': '--radius-sm',
    '--lemma-widget-radius-md': '--radius-md',
    '--lemma-widget-radius': '--radius-lg',
    '--lemma-widget-radius-panel': '--radius-xl',
    '--lemma-widget-duration-control': '--dur-control',
    '--lemma-widget-duration-panel': '--dur-panel',
    '--lemma-widget-duration-data': '--dur-data',
    '--lemma-widget-ease-standard': '--ease-standard',
    '--lemma-widget-ease-emphasized': '--ease-emphasized',
    // The real chart ramp, not brand hues and a status colour standing in for
    // one. `--state-success` as "series 3" both steals a meaning — a green bar
    // that encodes nothing still reads as "good" — and leaves the categorical
    // set without a step chosen to separate from its neighbours.
    '--lemma-widget-chart-1': '--chart-1',
    '--lemma-widget-chart-2': '--chart-2',
    '--lemma-widget-chart-3': '--chart-3',
    '--lemma-widget-chart-4': '--chart-4',
    '--lemma-widget-chart-5': '--chart-5',
    // Figures and ids want the mono face; without this they fall back to the UI
    // face and a column of numbers stops lining up.
    '--lemma-widget-font-mono': '--font-mono',
    // A card with the host's own depth instead of a 1px outline.
    '--lemma-widget-shadow-rest': '--shadow-md',
    '--lemma-widget-shadow-raise': '--shadow-lg',
    // No `on-accent` / `on-success` / `on-danger` here: this frontend has no
    // token for the ink that goes on a fill, and a guessed one is worse than
    // none — the widget's own fallback is at least known to pair. Anything
    // without an answer is left out rather than invented.
} as const;

export interface WidgetThemeMessage {
    type: typeof WIDGET_THEME_MESSAGE_TYPE;
    theme: 'light' | 'dark';
    tokens: Record<string, string>;
}

export function resolveWidgetTheme(
    resolvedTheme: string | undefined,
    systemPrefersDark: boolean,
): 'light' | 'dark' {
    if (resolvedTheme === 'dark') return 'dark';
    if (resolvedTheme === 'light') return 'light';
    return systemPrefersDark ? 'dark' : 'light';
}

export function buildWidgetThemeMessage({
    theme,
    readToken,
    fontFamily,
}: {
    theme: 'light' | 'dark';
    readToken: (name: string) => string;
    fontFamily: string;
}): WidgetThemeMessage {
    const tokens: Record<string, string> = {};
    Object.entries(WIDGET_THEME_TOKEN_SOURCES).forEach(([widgetToken, sourceToken]) => {
        const value = readToken(sourceToken).trim();
        if (value) tokens[widgetToken] = value;
    });

    const normalizedFont = fontFamily.trim();
    if (normalizedFont) tokens['--lemma-widget-font'] = normalizedFont;
    tokens['--lemma-widget-color-scheme'] = theme;
    tokens['--lemma-widget-danger-soft'] = theme === 'dark' ? '#331919' : '#fef2f2';

    return {
        type: WIDGET_THEME_MESSAGE_TYPE,
        theme,
        tokens,
    };
}
