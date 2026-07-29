export const SOCIAL_CARD_WIDTH = 1200;
export const SOCIAL_CARD_HEIGHT = 630;
export const SOCIAL_CARD_COLORS = {
    ink: '#11110f',
    muted: '#595851',
    paper: '#f3f1ea',
    panel: '#e9e6dc',
    card: '#f8f7f2',
    border: '#c9c5ba',
    rule: '#c5c1b6',
    placeholder: '#bbb7ac',
    greenSoft: '#e8f0d9',
    greenBorder: '#b7c49d',
    greenDot: '#66833e',
    greenLine: '#587137',
    blueSoft: '#e4ebf2',
    blueBorder: '#b3c1d0',
    blueDot: '#4a6580',
    blueLine: '#405b74',
} as const;

export type SocialCardVariant = 'site' | 'run' | 'build' | 'made' | 'join';

export interface SocialCardCopy {
    eyebrow: string;
    title: string;
    detail: string;
    label: string;
}

const VARIANT_COPY: Record<SocialCardVariant, SocialCardCopy> = {
    site: {
        eyebrow: 'LEMMA',
        title: 'Run it on Lemma.',
        detail: 'Run your apps and agents. Bring your team.',
        label: 'lemma.work',
    },
    run: {
        eyebrow: 'RUN IT ON LEMMA',
        title: 'A Lemma pod',
        detail: 'Apps · agents · workflows · data',
        label: 'lemma.work',
    },
    build: {
        eyebrow: 'BUILD ON LEMMA',
        title: 'Build what the work needs.',
        detail: 'Open tools for agent-built software.',
        label: 'lemma.work/docs',
    },
    made: {
        eyebrow: 'MADE WITH LEMMA',
        title: 'Work, running.',
        detail: 'Built by agents. Used by teams.',
        label: 'lemma.work',
    },
    join: {
        eyebrow: 'JOIN ON LEMMA',
        title: 'You have been invited.',
        detail: 'Work with the team and its agents.',
        label: 'lemma.work',
    },
};

function compact(value: string | null, fallback: string, limit: number): string {
    const normalized = value?.replace(/\s+/g, ' ').trim() || fallback;
    return normalized.length > limit
        ? `${normalized.slice(0, limit - 1).trimEnd()}…`
        : normalized;
}

export function isSocialCardVariant(value: string | null): value is SocialCardVariant {
    return value === 'site' || value === 'run' || value === 'build' || value === 'made' || value === 'join';
}

export function resolveSocialCardCopy(input: {
    variant?: string | null;
    title?: string | null;
    detail?: string | null;
    label?: string | null;
}): SocialCardCopy {
    const requestedVariant = input.variant ?? null;
    const variant: SocialCardVariant = isSocialCardVariant(requestedVariant)
        ? requestedVariant
        : 'run';
    const defaults = VARIANT_COPY[variant];
    return {
        eyebrow: defaults.eyebrow,
        title: compact(input.title ?? null, defaults.title, 64),
        detail: compact(input.detail ?? null, defaults.detail, 100),
        label: compact(input.label ?? null, defaults.label, 120),
    };
}

export function socialCardPath(input: {
    variant: SocialCardVariant;
    title?: string | null;
    detail?: string | null;
    label?: string | null;
}): string {
    const params = new URLSearchParams({ variant: input.variant });
    if (input.title) params.set('title', input.title);
    if (input.detail) params.set('detail', input.detail);
    if (input.label) params.set('label', input.label);
    return `/api/social-card?${params.toString()}`;
}
