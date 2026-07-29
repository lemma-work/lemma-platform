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

// Keep the catalogue empty until a template has a verified, public GitHub
// bundle that completes the same publish -> import -> apply release smoke test.
export const PUBLIC_TEMPLATES: PublicTemplate[] = [];

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
