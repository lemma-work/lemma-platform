/**
 * Structured content for /about and /contact — the two trust-anchor pages the
 * site did not have. Same `PageDocument` shape docs and legal pages use, so
 * both the visual page and its markdown negotiation variant (see
 * lib/markdown/render.ts) render from one source.
 */
import { COMPANY_DESCRIPTION, COMPANY_LEGAL_NAME } from '@/lib/company';
import { config } from '@/lib/config';
import type { PageDocument } from '@/lib/data/legal';

export const aboutPage: PageDocument = {
    title: 'About Lemma',
    description:
        'Lemma is the runtime for agent-built software: a coding agent writes the app, and Lemma gives it somewhere to live that a whole team can use.',
    sections: [
        {
            title: 'The bottleneck moved',
            body: 'A coding agent can write a working tool in an afternoon. Getting it to nine people — with the right access, still running tomorrow, reachable where the team already works — used to be the rest of the project. Lemma is built to close that gap: on Lemma, shared means running.',
        },
        {
            title: 'What a pod is',
            body: 'A pod is the unit Lemma runs: durable tables and records, files, deterministic functions, judgment-heavy agents, multi-step workflows, permissions, and apps, all addressable from the same place. An agent that builds against a pod is building something a team can operate, not a one-off script.',
        },
        {
            title: 'One pod, every surface',
            body: 'Slack, ChatGPT, Claude, Telegram, WhatsApp, email, a custom app, or a direct API call — each one reads and writes the same pod, under the same permissions. The record does not fork depending on how someone reached it.',
        },
        {
            title: 'Open source, run anywhere',
            body: 'Lemma runs on your own machine, your own server, or Lemma Cloud. The core is AGPLv3; the SDKs are Apache-2.0. Nothing about the architecture requires our hosting or our model provider.',
        },
        {
            title: 'Who makes it',
            body: `Lemma is made by ${COMPANY_DESCRIPTION} ("we", "us"), operating as ${COMPANY_LEGAL_NAME}. Questions, feedback, or press inquiries: see the Contact page, or write to ${config.SUPPORT_EMAIL}.`,
        },
    ],
};

export const contactPage: PageDocument = {
    title: 'Contact',
    description: 'How to reach Lemma — support, security, and everything else.',
    sections: [
        {
            title: 'Support and general questions',
            body: `Product questions, account issues, bug reports, and feedback all go to the same address: ${config.SUPPORT_EMAIL}. A person reads every message.`,
        },
        {
            title: 'Security',
            body: `Found a vulnerability? See SECURITY.md in the GitHub repository for the disclosure process, or write directly to ${config.SUPPORT_EMAIL} with "security" in the subject line.`,
        },
        {
            title: 'Sales and partnerships',
            body: `For Lemma Cloud plans, enterprise deployments, or partnership inquiries, write to ${config.SUPPORT_EMAIL} — say what you are trying to do and we will route it to the right person.`,
        },
        {
            title: 'Privacy and data requests',
            body: `Requests under GDPR, the UK GDPR, or the CCPA — a copy of your data, a correction, or a deletion — go to ${config.SUPPORT_EMAIL}. Full detail on what we collect and why is on the Privacy page.`,
        },
        {
            title: 'The company behind Lemma',
            body: `Lemma is a product of ${COMPANY_LEGAL_NAME}.`,
        },
    ],
};
