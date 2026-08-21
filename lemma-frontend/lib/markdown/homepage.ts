import { githubUrl, surfaceModes } from '@/components/landing/landing-data';
import { SITE_DESCRIPTION, SITE_TITLE } from '@/lib/seo/site-copy';

/**
 * The markdown representation of `/`. The landing page itself is an
 * animated, highly visual scene-by-scene pitch — this is not a scrape of it,
 * it is the same thesis and the same facts (surfaceModes below is the exact
 * data landing-page.tsx renders) in a shape an agent can read in one pass.
 */
export function homepageMarkdown(): string {
    const surfaces = surfaceModes.map((surface) => `- **${surface.label}** — ${surface.body}`).join('\n');

    return `# Lemma

> ${SITE_TITLE} ${SITE_DESCRIPTION}

Lemma is an open-source runtime for agent-built software. A coding agent
writes the app; Lemma gives it a pod — durable tables, files, functions,
agents, workflows, permissions, and apps — so what the agent built keeps
running and your team can actually use it, instead of living only in a chat
transcript.

## What a pod looks like from outside

Nine surfaces read and write the same pod, under the same permissions:

${surfaces}

## Start here

- [Docs](/docs): platform concepts, SDK reference, CLI reference, and guides.
- [Quickstart](/docs/getting-started): create an account, run Lemma locally or in the cloud, and create your first pod.
- [OpenAPI spec](/openapi.json): the full Lemma Cloud API surface.
- [llms.txt](/llms.txt): a machine-readable index of this site for agents.
- [GitHub](${githubUrl}): source, issues, and the AGPLv3/Apache-2.0 license split.
- [Templates](/templates): pods you can import and remix.
`;
}
