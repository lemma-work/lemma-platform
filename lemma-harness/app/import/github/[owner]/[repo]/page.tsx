import type { Metadata } from 'next';

import { ImportGithubClient } from './import-github-client';
import { fetchPublicGitHubReadme } from '@/lib/github/public-repository';
import { socialCardPath } from '@/lib/share/social-card';
import { findPublicTemplateBySource } from '@/lib/templates/catalog';

interface ImportGithubPageProps {
    params: Promise<{ owner: string; repo: string }>;
    searchParams: Promise<{ destination?: string | string[] }>;
}

function decodeSegment(value: string): string {
    try {
        return decodeURIComponent(value);
    } catch {
        return value;
    }
}

export async function generateMetadata({ params }: ImportGithubPageProps): Promise<Metadata> {
    const raw = await params;
    const owner = decodeSegment(raw.owner);
    const repo = decodeSegment(raw.repo);
    const repoLabel = `github.com/${owner}/${repo}`;
    const image = socialCardPath({
        variant: 'run',
        title: repo,
        detail: 'A complete pod, ready to run with your team.',
        label: repoLabel,
    });

    // This route renders for *any* repository on GitHub, and most of what it
    // shows is someone else's README. Indexing the whole space would publish an
    // unbounded set of pages whose real content is canonical on github.com —
    // the textbook shape of auto-generated duplicate content. The ten curated
    // templates are pages we author the framing for and stand behind, so they
    // are the ones declared to search; everything else stays crawlable and
    // link-following but out of the index.
    const curated = findPublicTemplateBySource(owner, repo) !== null;

    return {
        title: `Run ${repo} on Lemma`,
        description: `Import ${owner}/${repo} into Lemma and run its apps, agents, workflows, and data.`,
        alternates: {
            canonical: `/import/github/${encodeURIComponent(owner)}/${encodeURIComponent(repo)}`,
        },
        ...(curated ? {} : { robots: { index: false, follow: true } }),
        openGraph: {
            title: `Run ${repo} on Lemma.`,
            description: 'A complete pod, ready to run with your team.',
            type: 'website',
            images: [{ url: image, width: 1200, height: 630, alt: `Run ${repo} on Lemma` }],
        },
        twitter: {
            card: 'summary_large_image',
            title: `Run ${repo} on Lemma.`,
            description: 'A complete pod, ready to run with your team.',
            images: [image],
        },
    };
}

export default async function ImportGithubPage({ params, searchParams }: ImportGithubPageProps) {
    const [raw, query] = await Promise.all([params, searchParams]);
    const owner = decodeSegment(raw.owner);
    const repo = decodeSegment(raw.repo);
    const initialReadme = await fetchPublicGitHubReadme(owner, repo);

    return (
        <ImportGithubClient
            owner={owner}
            repo={repo}
            initialDestination={query.destination === 'existing' ? 'existing' : 'new'}
            initialReadme={initialReadme}
        />
    );
}
