/**
 * Structured content for /about and /contact — the two trust-anchor pages the
 * site did not have. Same `PageDocument` shape docs and legal pages use, so
 * both the visual page and its markdown negotiation variant (see
 * lib/markdown/render.ts) render from one source.
 */
import { COMPANY_DESCRIPTION, COMPANY_LEGAL_NAME } from "@/site/company";
import { config } from "@/site/config";
import type { PageDocument } from "@/site/data/legal";

export const aboutPage: PageDocument = {
    title: "About Lemma",
    description:
        "Lemma is an open-source, model-agnostic platform for creating, training, and sharing AI coworkers.",
    sections: [
        {
            title: "Add a coworker. Train it on the job. Share it with your team.",
            body: "Give a coworker a responsibility, teach it how your team works, and share it with the people who need it. Each coworker is backed by a multiplayer harness built for its specific job: a persistent environment of context, memory, tools, apps, workflows, and permissions that your team and the coworker use together.",
        },
        {
            title: "What a multiplayer harness means",
            body: "The harness is the environment around the model that makes ongoing work possible. It supplies the coworker with the data, files, tools, connected accounts, and automation needed for its job. Multiplayer means people can work with the same coworker and shared resources, review results, and continue the work from different conversations and channels, with access governed by permissions. A support coworker might use a shared inbox, customer records, reply tools, and approval steps; a launch coworker might use a campaign plan, asset library, and review app.",
        },
        {
            title: "What training on the job means",
            body: "Teach the coworker through instructions, examples, shared documents, corrections, and feedback. Capture what it should keep using in its instructions and persistent workspace resources, and refine its tools and workflows as the job develops.",
        },

        {
            title: "What people use Lemma for",
            body: "Give an AI teammate a responsibility, such as preparing a product launch, researching prospects, triaging support requests, or coordinating customer onboarding. People provide context, connect the tools it needs, review its work, and follow up in the same workspace. The teammate can build and use apps and automation as part of that responsibility.",
        },
        {
            title: "How work continues",
            body: "A person sends a request, or a configured schedule or event starts a run. The agent reads the context it can access, uses tools, and records results in the workspace. People can inspect the result, correct it, and continue the conversation. Files and records persist beyond an individual run, so later work can use them as context. Approvals and human review can be configured where the workflow requires them.",
        },
        {
            title: "What the building blocks do",
            body: "Tables and records hold structured data. Files hold documents, code, and other artifacts. Functions execute defined operations. Agents interpret requests and use tools. Workflows coordinate steps; schedules start work at configured times. Apps provide interfaces for people to view and change workspace data. Connectors provide access to external services through connected accounts.",
        },
        {
            title: "Where people interact with a teammate",
            body: "People can use the Lemma workspace or configured channels such as Slack, Microsoft Teams, Telegram, WhatsApp, and email. Those channels reach the same underlying workspace. Access depends on the requesting person, the agent's configured tools, the channel setup, and resource permissions. Connecting a channel does not grant unrestricted access to everything in the pod.",
        },
        {
            title: "For agents and developers integrating with Lemma",
            body: "Start with the [documentation](/docs) for the resource model, the [TypeScript and Python SDK guide](/docs/sdk/installation) for integrations, and the [CLI guide](/docs/cli/overview) for terminal operations. The [OpenAPI specification](/openapi.json) describes HTTP operations. API operations require the appropriate authenticated organization and pod context and remain subject to permissions. [llms.txt](/llms.txt) indexes the machine-readable entry points. This About page also supports an Accept: text/markdown request.",
        },
        {
            title: "Hosting, models, and source code",
            body: "Use Lemma Cloud, run Lemma locally, or self-host it on a server. Model-agnostic means a coworker is not tied to one model provider. It can use supported hosted models, connected provider accounts, or a paired computer running a supported coding agent, depending on the deployment and configuration. Its workspace resources persist when its runtime changes. Lemma provides the workspace and orchestration; it is not a foundation-model provider. The [platform source](https://github.com/lemma-work/lemma-platform) is AGPLv3, and the SDKs are Apache 2.0. See the [setup guide](/docs/getting-started) for deployment options.",
        },
        {
            title: "Who makes it",
            body: `Lemma is made by ${COMPANY_DESCRIPTION} ("we", "us"), operating as ${COMPANY_LEGAL_NAME}. Questions, feedback, or press inquiries: see the Contact page, or write to ${config.SUPPORT_EMAIL}.`,
        },
    ],
};

export const contactPage: PageDocument = {
    title: "Contact",
    description: "How to reach Lemma — support, security, and everything else.",
    sections: [
        {
            title: "Support and general questions",
            body: `Product questions, account issues, bug reports, and feedback all go to the same address: ${config.SUPPORT_EMAIL}. A person reads every message.`,
        },
        {
            title: "Security",
            body: `Found a vulnerability? See SECURITY.md in the GitHub repository for the disclosure process, or write directly to ${config.SUPPORT_EMAIL} with "security" in the subject line.`,
        },
        {
            title: "Sales and partnerships",
            body: `For Lemma Cloud plans, enterprise deployments, or partnership inquiries, write to ${config.SUPPORT_EMAIL} — say what you are trying to do and we will route it to the right person.`,
        },
        {
            title: "Privacy and data requests",
            body: `Requests under GDPR, the UK GDPR, or the CCPA — a copy of your data, a correction, or a deletion — go to ${config.SUPPORT_EMAIL}. Full detail on what we collect and why is on the Privacy page.`,
        },
        {
            title: "The company behind Lemma",
            body: `Lemma is a product of ${COMPANY_LEGAL_NAME}.`,
        },
    ],
};
