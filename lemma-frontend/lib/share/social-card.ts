/**
 * The card people see before they see the product.
 *
 * One renderer serves every surface: Open Graph tags, the in-product share
 * preview, and the PNG people copy into a post. `/api/social-card` is that
 * renderer — this module is the spec it draws from, so a card can never drift
 * between the link unfurl and the image someone downloads.
 */

export const SOCIAL_CARD_WIDTH = 1200;
export const SOCIAL_CARD_HEIGHT = 630;

/**
 * Card colours are frozen literals, not theme tokens. The card renders in
 * Satori on the edge with no CSS custom properties, and it must look the same
 * in a dark-mode timeline as in a light one.
 */
export const SOCIAL_CARD_COLORS = {
    ink: '#181816',
    inkSoft: '#3c3b38',
    muted: '#6f6d68',
    faint: '#99968f',
    paper: '#f4f3ef',
    panel: '#eeede7',
    card: '#ffffff',
    border: '#d9d7d1',
    rule: '#cfcdc6',
    grid: '#181816',
} as const;

/**
 * The pod stack, bottom to top — the same order the landing page builds it in:
 * what it remembers, what it does the same way every time, what moves the work,
 * where people meet it. The card draws these as receding layers.
 */
export const SOCIAL_CARD_LAYERS = [
    { key: 'data', label: 'Data', color: '#6ea5c1' },
    { key: 'functions', label: 'Functions', color: '#32c98a' },
    { key: 'agents', label: 'Agents', color: '#e4b52d' },
    { key: 'apps', label: 'Apps', color: '#5f61d8' },
] as const;

export type SocialCardLayerKey = (typeof SOCIAL_CARD_LAYERS)[number]['key'];

export type SocialCardVariant =
    | 'site'
    | 'run'
    | 'build'
    | 'made'
    | 'join'
    | 'app'
    | 'agent'
    | 'workflow'
    | 'function'
    | 'table'
    | 'document'
    | 'schedule';

export interface SocialCardCopy {
    eyebrow: string;
    title: string;
    detail: string;
    label: string;
}

export interface SocialCardSpec extends SocialCardCopy {
    variant: SocialCardVariant;
    /** Drives the eyebrow, the accent rule and the highlighted pod layer. */
    accent: string;
    /** Which pod layer rides on top of the stack — the thing being shared. */
    layer: SocialCardLayerKey;
}

const VARIANT_COPY: Record<
    SocialCardVariant,
    SocialCardCopy & { accent: string; layer: SocialCardLayerKey }
> = {
    site: {
        eyebrow: 'THE RUNTIME FOR AGENT-BUILT SOFTWARE',
        title: "Where software builds itself.",
        detail: 'Your coding agent can write it. Lemma makes it something your team can actually use.',
        label: 'lemma.work',
        accent: '#5f61d8',
        layer: 'apps',
    },
    run: {
        eyebrow: 'RUN IT ON LEMMA',
        title: 'A Lemma pod',
        detail: 'Apps · agents · workflows · data',
        label: 'lemma.work',
        accent: '#5f61d8',
        layer: 'apps',
    },
    build: {
        eyebrow: 'BUILD ON LEMMA',
        title: 'Build what the work needs.',
        detail: 'Open tools for agent-built software.',
        label: 'lemma.work/docs',
        accent: '#32c98a',
        layer: 'functions',
    },
    made: {
        eyebrow: 'MADE WITH LEMMA',
        title: 'Work, running.',
        detail: 'Built by agents. Used by teams.',
        label: 'lemma.work',
        accent: '#e4b52d',
        layer: 'agents',
    },
    join: {
        eyebrow: 'JOIN ON LEMMA',
        title: 'You have been invited.',
        detail: 'Work with the team and its agents.',
        label: 'lemma.work',
        accent: '#f06b3e',
        layer: 'apps',
    },
    app: {
        eyebrow: 'AN APP ON LEMMA',
        title: 'A Lemma app',
        detail: 'Built on a pod. Open it and start working.',
        label: 'lemma.work',
        accent: '#5f61d8',
        layer: 'apps',
    },
    agent: {
        eyebrow: 'AN AGENT ON LEMMA',
        title: 'A Lemma agent',
        detail: 'Give it the work. Watch what it does.',
        label: 'lemma.work',
        accent: '#e4b52d',
        layer: 'agents',
    },
    workflow: {
        eyebrow: 'A WORKFLOW ON LEMMA',
        title: 'A Lemma workflow',
        detail: 'The same steps, every single time.',
        label: 'lemma.work',
        accent: '#32c98a',
        layer: 'functions',
    },
    function: {
        eyebrow: 'A FUNCTION ON LEMMA',
        title: 'A Lemma function',
        detail: 'One job, done the same way every time.',
        label: 'lemma.work',
        accent: '#32c98a',
        layer: 'functions',
    },
    table: {
        eyebrow: 'A TABLE ON LEMMA',
        title: 'A Lemma table',
        detail: 'The data the work runs on.',
        label: 'lemma.work',
        accent: '#6ea5c1',
        layer: 'data',
    },
    document: {
        eyebrow: 'A DOCUMENT ON LEMMA',
        title: 'A Lemma document',
        detail: 'Filed where the agents can read it.',
        label: 'lemma.work',
        accent: '#6ea5c1',
        layer: 'data',
    },
    schedule: {
        eyebrow: 'A SCHEDULE ON LEMMA',
        title: 'A Lemma schedule',
        detail: 'Work that starts without being asked.',
        label: 'lemma.work',
        accent: '#e4b52d',
        layer: 'agents',
    },
};

const VARIANTS = Object.keys(VARIANT_COPY) as SocialCardVariant[];

function compact(value: string | null, fallback: string, limit: number): string {
    const normalized = value?.replace(/\s+/g, ' ').trim() || fallback;
    return normalized.length > limit
        ? `${normalized.slice(0, limit - 1).trimEnd()}…`
        : normalized;
}

export function isSocialCardVariant(value: string | null): value is SocialCardVariant {
    return VARIANTS.includes(value as SocialCardVariant);
}

export function resolveSocialCardCopy(input: {
    variant?: string | null;
    title?: string | null;
    detail?: string | null;
    label?: string | null;
}): SocialCardCopy {
    const { eyebrow, title, detail, label } = resolveSocialCardSpec(input);
    return { eyebrow, title, detail, label };
}

/** The full drawing instruction: copy plus the accent the renderer paints with. */
export function resolveSocialCardSpec(input: {
    variant?: string | null;
    title?: string | null;
    detail?: string | null;
    label?: string | null;
}): SocialCardSpec {
    const requestedVariant = input.variant ?? null;
    const variant: SocialCardVariant = isSocialCardVariant(requestedVariant)
        ? requestedVariant
        : 'run';
    const defaults = VARIANT_COPY[variant];
    return {
        variant,
        accent: defaults.accent,
        layer: defaults.layer,
        eyebrow: defaults.eyebrow,
        title: compact(input.title ?? null, defaults.title, 64),
        detail: compact(input.detail ?? null, defaults.detail, 100),
        label: compact(input.label ?? null, defaults.label, 120),
    };
}

/**
 * Title size steps down as the name grows so a long pod name still lands on two
 * lines instead of overflowing the plate.
 */
export function socialCardTitleSize(title: string): number {
    if (title.length > 46) return 58;
    if (title.length > 34) return 68;
    if (title.length > 22) return 78;
    return 88;
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

/** Absolute variant, for clipboards, downloads and `<meta>` tags that need one. */
export function socialCardUrl(
    input: Parameters<typeof socialCardPath>[0],
    origin?: string,
): string {
    const path = socialCardPath(input);
    const base = origin ?? (typeof window === 'undefined' ? '' : window.location.origin);
    return base ? new URL(path, base).toString() : path;
}

export function socialCardFilename(name?: string | null): string {
    const slug = (name?.replace(/\s+/g, ' ').trim() || 'lemma')
        .toLowerCase()
        .replace(/[^a-z0-9]+/g, '-')
        .replace(/^-+|-+$/g, '')
        .slice(0, 64);
    return `${slug || 'lemma'}-share-card.png`;
}
