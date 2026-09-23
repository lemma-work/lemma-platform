import { docsGroups, docsPageMap } from '@/lib/data/docs';

/** The markdown representation of `/docs` — every group, and every page in it. */
export function docsIndexMarkdown(): string {
    const groups = docsGroups
        .map((group) => {
            const pages = group.pages
                .map((slug) => docsPageMap.get(slug))
                .filter((page): page is NonNullable<typeof page> => Boolean(page))
                .map((page) => `- [${page.title}](/docs/${page.slug}): ${page.description}`)
                .join('\n');
            return `## ${group.title}\n\n${pages}`;
        })
        .join('\n\n');

    return `# Lemma Docs

> Platform concepts, the TypeScript and Python SDKs, the CLI, and build guides for Lemma — the runtime for agent-built software.

${groups}
`;
}
