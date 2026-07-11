export const WIDGET_THEME_MESSAGE_TYPE = 'lemma-widget-theme';

const WIDGET_THEME_TOKEN_SOURCES = {
    '--lemma-widget-bg': '--pod-main-bg',
    '--lemma-widget-surface': '--surface-1',
    '--lemma-widget-subtle': '--surface-2',
    '--lemma-widget-text': '--text-primary',
    '--lemma-widget-muted': '--text-secondary',
    '--lemma-widget-border': '--border-subtle',
    '--lemma-widget-accent': '--brand-primary',
    '--lemma-widget-danger': '--state-error',
    '--lemma-widget-radius': '--radius-lg',
    '--lemma-widget-chart-1': '--brand-primary',
    '--lemma-widget-chart-2': '--brand-sky',
    '--lemma-widget-chart-3': '--brand-accent',
    '--lemma-widget-chart-4': '--brand-coral',
    '--lemma-widget-chart-5': '--brand-lilac',
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
