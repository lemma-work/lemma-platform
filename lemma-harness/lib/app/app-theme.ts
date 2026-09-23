export const APP_THEME_MESSAGE_TYPE = 'lemma-app-theme';

const APP_THEME_TOKEN_SOURCES = {
    '--lemma-app-bg': '--pod-main-bg',
    '--lemma-app-surface': '--surface-1',
    '--lemma-app-subtle': '--surface-2',
    '--lemma-app-raised': '--surface-3',
    '--lemma-app-text': '--text-primary',
    '--lemma-app-muted': '--text-secondary',
    '--lemma-app-faint': '--text-tertiary',
    '--lemma-app-text-on-accent': '--text-on-brand',
    '--lemma-app-border': '--border-subtle',
    '--lemma-app-border-strong': '--border-default',
    '--lemma-app-accent': '--interactive-primary',
    '--lemma-app-accent-hover': '--action-primary-hover',
    '--lemma-app-accent-soft': '--action-primary-soft',
    '--lemma-app-success': '--state-success',
    '--lemma-app-warning': '--state-warning',
    '--lemma-app-danger': '--state-error',
    '--lemma-app-info': '--state-info',
    '--lemma-app-chart-1': '--brand-coral',
    '--lemma-app-chart-2': '--brand-lilac',
    '--lemma-app-chart-3': '--state-success',
    '--lemma-app-chart-4': '--brand-accent',
    '--lemma-app-chart-5': '--text-tertiary',
    '--lemma-app-radius-sm': '--radius-sm',
    '--lemma-app-radius-md': '--radius-md',
    '--lemma-app-radius-lg': '--radius-lg',
    '--lemma-app-radius-panel': '--radius-xl',
    '--lemma-app-duration-control': '--dur-control',
    '--lemma-app-duration-panel': '--dur-panel',
    '--lemma-app-duration-data': '--dur-data',
    '--lemma-app-ease-standard': '--ease-standard',
    '--lemma-app-ease-emphasized': '--ease-emphasized',
} as const;

export interface AppThemeMessage {
    type: typeof APP_THEME_MESSAGE_TYPE;
    theme: 'light' | 'dark';
    density: 'compact';
    tokens: Record<string, string>;
}

export function buildAppThemeMessage({
    theme,
    readToken,
    fontFamily,
}: {
    theme: 'light' | 'dark';
    readToken: (name: string) => string;
    fontFamily: string;
}): AppThemeMessage {
    const tokens: Record<string, string> = {};
    Object.entries(APP_THEME_TOKEN_SOURCES).forEach(([appToken, sourceToken]) => {
        const value = readToken(sourceToken).trim();
        if (value) tokens[appToken] = value;
    });

    const normalizedFont = fontFamily.trim();
    if (normalizedFont) tokens['--lemma-app-font'] = normalizedFont;
    tokens['--lemma-app-color-scheme'] = theme;

    return {
        type: APP_THEME_MESSAGE_TYPE,
        theme,
        density: 'compact',
        tokens,
    };
}
