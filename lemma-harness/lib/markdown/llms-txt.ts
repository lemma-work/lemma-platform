import { githubUrl } from '@/components/landing/landing-data';
import { absoluteUrl } from '@/lib/seo/site-url';

/**
 * /llms.txt, per https://llmstxt.org: an H1, a one-paragraph blockquote
 * summary, optional prose, then H2-delimited link sections. "When to use
 * Lemma" is the section an audit like this one is checking for by name — it
 * has to say which jobs Lemma actually fits, not restate the homepage pitch.
 */
export function llmsTxt(): string {
    return `# Lemma

> Lemma is the runtime for agent-built software: a coding agent writes the app, and Lemma gives it a pod — durable tables, files, functions, agents, workflows, permissions, and apps — so a team can run it, not just the person who prompted it.

Open source (AGPLv3 core, Apache-2.0 SDKs). Runs on your own machine, your own server, or Lemma Cloud.

## When to use Lemma

- Turning a coding agent's output into something a team can operate day to day: support triage, lead qualification, expense review, onboarding, launch tracking, and similar back-office operating loops.
- Giving an agent durable state (tables, files) and deterministic actions (functions) instead of re-deriving everything from chat history on every run.
- Reaching one system from wherever the work already happens — Slack, ChatGPT, Claude, Telegram, WhatsApp, email, or a purpose-built app — under one permission model, instead of copies of the data forking per surface.
- Calling a product programmatically: the SDKs and CLI are the primary integration path, not a UI meant only for humans.
- Skip Lemma for a single-session chat agent with no persistent state, no team of users, and no automation beyond that one conversation — that is simpler done directly against a model provider.

## Docs

- [Documentation](${absoluteUrl('/docs')}): platform concepts, SDK reference, CLI reference, and build guides.
- [Quickstart](${absoluteUrl('/docs/getting-started')}): create an account, run Lemma locally or in the cloud, install the CLI, and create a pod.
- [Overview](${absoluteUrl('/docs/overview')}): the mental model — pods, tables, files, functions, agents, workflows, apps.
- [SDK Installation](${absoluteUrl('/docs/sdk/installation')}): install and configure \`lemma-sdk\` (TypeScript) or \`lemma-sdk\` (Python).
- [SDK Core Client](${absoluteUrl('/docs/sdk/client')}): the LemmaClient namespace map.
- [CLI Overview](${absoluteUrl('/docs/cli/overview')}): command groups and global options for \`lemma-terminal\`.

## API

- [OpenAPI spec](${absoluteUrl('/openapi.json')}): the full Lemma Cloud API surface, one operationId and description per operation.
- [Auth and Context](${absoluteUrl('/docs/cli/auth-and-context')}): authenticate the CLI and inspect org/pod context.
- [React Auth and Pod Access](${absoluteUrl('/docs/sdk/react-auth')}): AuthGuard and pod-access hooks for app frontends.

## Optional

- [About](${absoluteUrl('/about')}): who makes Lemma.
- [Contact](${absoluteUrl('/contact')}): support, security, sales, and privacy contacts.
- [Privacy](${absoluteUrl('/privacy')})
- [Terms of Service](${absoluteUrl('/tos')})
- [Changelog](${absoluteUrl('/changelog')}): every release, newest first.
- [Blog](${absoluteUrl('/blog')})
- [GitHub](${githubUrl}): source, issues, and the license split.
`;
}
