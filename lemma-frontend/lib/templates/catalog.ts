export interface PublicTemplate {
    slug: string;
    name: string;
    kicker: string;
    description: string;
    github: string;
    outcomes: string[];
    includes: Array<{
        label: string;
        detail: string;
    }>;
}

export const PUBLIC_TEMPLATES: PublicTemplate[] = [
    {
        slug: 'research-desk',
        name: 'Research Desk',
        kicker: 'Source-backed research that keeps moving',
        description:
            'Collect evidence, investigate a question, and turn what you find into briefs your team can use.',
        github: 'https://github.com/lemma-work/research-desk',
        outcomes: [
            'One durable home for sources, notes, questions, and briefs',
            'An agent that researches against evidence instead of guessing',
            'A shared queue so teammates can contribute and review',
        ],
        includes: [
            {
                label: 'Research app',
                detail: 'A focused desk for questions, evidence, notes, and finished briefs.',
            },
            {
                label: 'Research agent',
                detail: 'Investigates open questions and links every useful claim back to a source.',
            },
            {
                label: 'Evidence workflow',
                detail: 'Moves work from capture through review to a shareable, source-backed result.',
            },
            {
                label: 'Shared data',
                detail: 'Keeps sources, findings, and decisions available to the whole team.',
            },
        ],
    },
];

export function getPublicTemplateBySlug(slug: string | null | undefined): PublicTemplate | null {
    return PUBLIC_TEMPLATES.find((template) => template.slug === slug) ?? null;
}

export function templateRunHref(template: PublicTemplate): string {
    const source = new URL(template.github);
    if (source.hostname.toLowerCase() !== 'github.com') {
        throw new Error(`Template "${template.slug}" must use a github.com source.`);
    }
    const [owner, repoWithSuffix] = source.pathname.split('/').filter(Boolean);
    const repo = repoWithSuffix?.replace(/\.git$/i, '');
    if (!owner || !repo) {
        throw new Error(`Template "${template.slug}" has an invalid GitHub source.`);
    }
    return `/import/github/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`;
}
