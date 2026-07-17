import type { ProductIconKind } from '@/components/pod/product-icon';

export type ConceptId =
    | 'pod'
    | 'agent'
    | 'flow'
    | 'table'
    | 'file'
    | 'surface'
    | 'app'
    | 'kit'
    | 'function'
    | 'schedule'
    | 'connector'
    | 'conversation'
    | 'grant'
    | 'scope'
    | 'approval'
    | 'runtime'
    | 'variable';

export interface ConceptEntry {
    id: ConceptId;
    term: string;
    oneLiner: string;
    explainer: string[];
    example: string;
    iconKind: ProductIconKind;
    guideSlug: string;
    related: ConceptId[];
}

export const CONCEPTS: Record<ConceptId, ConceptEntry> = {
    pod: {
        id: 'pod',
        term: 'Pod',
        oneLiner:
            'A self-contained workspace for one team or process — its agents, data, flows, and permissions live together.',
        explainer: [
            'Think of a pod as one operating unit of your business: support, hiring, ads reporting. Everything the work needs — AI workers, business data, automations, and access rules — is scoped inside it.',
            'Keeping each process in its own pod means agents only see the data they should, and you can share one pod without exposing another.',
        ],
        example:
            'A "Customer support" pod holds the triage agent, the Tickets table, the escalation flow, and the connected support inbox.',
        iconKind: 'pods',
        guideSlug: 'concepts/pods',
        related: ['agent', 'table', 'flow', 'grant'],
    },
    agent: {
        id: 'agent',
        term: 'Agent',
        oneLiner:
            'An AI worker with a role, instructions, and scoped access to your tables, files, and apps.',
        explainer: [
            'An agent is hired like a teammate: you write its instructions (the job description), pick its runtime, and grant it access to exactly the data and apps it needs.',
            'Agents can be asked things in chat, put on a schedule, given steps in a flow, or wired to a surface so they handle inbound work on their own.',
        ],
        example:
            'A triage agent reads each new support email, labels the issue, and files a row in the Tickets table.',
        iconKind: 'agents',
        guideSlug: 'concepts/agents',
        related: ['runtime', 'flow', 'surface', 'function'],
    },
    flow: {
        id: 'flow',
        term: 'Workflow',
        oneLiner:
            'A repeatable process: steps of agents, functions, decisions, and human approvals that run in order.',
        explainer: [
            'When work has more than one step, put it in a workflow instead of one big agent prompt. Each step does one job, and you can mix AI steps with deterministic functions and human approval gates.',
            'Workflows start from a schedule, a webhook, chat, or by hand — and every run is inspectable step by step.',
        ],
        example:
            'New lead arrives → agent enriches it → function scores it → if score is high, a human approves → agent drafts the outreach email.',
        iconKind: 'workflows',
        guideSlug: 'concepts/workflows',
        related: ['agent', 'function', 'approval', 'schedule'],
    },
    table: {
        id: 'table',
        term: 'Table',
        oneLiner:
            'Typed business data the pod reads and writes — leads, tickets, tasks — with per-row security.',
        explainer: [
            'Tables are where the pod keeps structured state. Columns are typed fields; rows are records that agents, flows, and people all read and update.',
            'Because access is controlled per row and per agent, an agent can work the Tickets table without ever seeing Payroll.',
        ],
        example:
            'A Leads table with name, company, source, and status — agents append rows, your team works them from an app.',
        iconKind: 'tables',
        guideSlug: 'concepts/tables',
        related: ['file', 'agent', 'app', 'grant'],
    },
    file: {
        id: 'file',
        term: 'File',
        oneLiner: 'Documents the pod can search, read, and cite in its answers.',
        explainer: [
            'Upload contracts, policies, specs, or exports and they become part of what the pod knows. Agents search files to ground their answers instead of guessing.',
            'Files live alongside tables: tables for structured records, files for documents.',
        ],
        example:
            'Upload the returns policy PDF and the support agent quotes the actual policy when replying to customers.',
        iconKind: 'files',
        guideSlug: 'concepts/files',
        related: ['table', 'agent'],
    },
    surface: {
        id: 'surface',
        term: 'Surface',
        oneLiner:
            'A channel where work reaches the pod: Slack, Gmail, WhatsApp, Telegram, Teams, Outlook.',
        explainer: [
            'Without a surface, work only enters Lemma when someone types here. Connect Slack or Gmail and the pod’s agents can pick up messages, triage them, and reply — in the place your team already works.',
            'Each surface is wired to a specific agent, so you control exactly what handles inbound work.',
        ],
        example:
            'Connect the support Gmail inbox; the triage agent labels each email and files a row in the Tickets table.',
        iconKind: 'surfaces',
        guideSlug: 'concepts/surfaces',
        related: ['agent', 'connector', 'conversation'],
    },
    app: {
        id: 'app',
        term: 'App',
        oneLiner:
            'A custom app where your team and the pod’s agents work together — built on this pod’s data, deployed at its own URL.',
        explainer: [
            'An app is where people and agents collaborate. A teammate drives the work — opening records, making the calls — and agents pitch in two ways: kick one off in the background with a button, or keep one open beside you like an assistant that drafts and answers as you go.',
            'Build it from the same agents, tables, and files as the rest of the pod, share the URL, and your team gets an AI-powered workspace without ever touching the configuration.',
        ],
        example:
            'A support app where every ticket opens with an agent’s draft reply already written — a teammate sends it, refines it, or asks the assistant for another angle.',
        iconKind: 'apps',
        guideSlug: 'concepts/apps',
        related: ['agent', 'table', 'grant'],
    },
    kit: {
        id: 'kit',
        term: 'Kit',
        oneLiner:
            'A whole pod setup compressed into something shareable — install it like a plugin and it builds out apps, agents, tables, workflows, and schedules.',
        explainer: [
            'Think of a kit as a pod’s working parts packaged so they drop into any other pod. Installing one can add apps, agents, tables, schedules, and workflows at once — whatever that kit bundles — already wired to work together.',
            'Everything it installs is yours to edit: rename the agents, rework the app, change the schedule, delete what you don’t need.',
        ],
        example:
            'The hiring kit installs a candidate table, a sourcing agent, a review app, and a weekly follow-up schedule in one click.',
        iconKind: 'pods',
        guideSlug: 'concepts/kits',
        related: ['pod', 'app', 'agent', 'schedule'],
    },
    function: {
        id: 'function',
        term: 'Function',
        oneLiner:
            'Deterministic code for steps that shouldn’t be AI judgment: validation, math, formatting.',
        explainer: [
            'Not everything should be left to a model. Functions are plain code the pod runs exactly the same way every time — perfect for scoring, validating, transforming, and calling APIs.',
            'Use agents for judgment, functions for rules. Flows mix both freely.',
        ],
        example:
            'A lead-scoring function applies your exact point rules, so the same lead always gets the same score.',
        iconKind: 'functions',
        guideSlug: 'concepts/functions',
        related: ['flow', 'agent'],
    },
    schedule: {
        id: 'schedule',
        term: 'Schedule',
        oneLiner:
            'A timer that runs an agent or flow automatically — daily digests, hourly syncs.',
        explainer: [
            'Schedules turn the pod from reactive to proactive. Pick an agent or flow, set when it should run, and it shows up under "Upcoming" on the pod home.',
            'Anything you find yourself asking for repeatedly is a schedule waiting to be created.',
        ],
        example:
            'Every weekday at 8am, the reporting agent posts yesterday’s ad spend summary to Slack.',
        iconKind: 'schedules',
        guideSlug: 'concepts/schedules',
        related: ['flow', 'agent', 'surface'],
    },
    connector: {
        id: 'connector',
        term: 'Connector',
        oneLiner:
            'An authenticated connection to a third-party app that agents and flows can act on.',
        explainer: [
            'Connectors are the pod’s hands: connect Salesforce, GitHub, or Notion once, and agents can read and act on them — within the operations you allow.',
            'Surfaces bring work in; connectors let the pod act out in your other tools.',
        ],
        example:
            'Connect Salesforce and the sales agent updates opportunity stages instead of just telling you to.',
        iconKind: 'connectors',
        guideSlug: 'concepts/connectors',
        related: ['surface', 'agent', 'grant'],
    },
    conversation: {
        id: 'conversation',
        term: 'Conversation',
        oneLiner:
            'A thread between you (or a surface) and this pod’s agents, with every tool call visible.',
        explainer: [
            'Every interaction with an agent — from this chat, Slack, email, or the API — becomes a conversation you can open and audit.',
            'You see not just what the agent said, but what it did: every table write, file search, and connector call.',
        ],
        example:
            'Open yesterday’s triage conversation to see exactly why the agent escalated ticket #482.',
        iconKind: 'conversation',
        guideSlug: 'concepts/conversations',
        related: ['agent', 'surface'],
    },
    grant: {
        id: 'grant',
        term: 'Sharing',
        oneLiner:
            'Who can see and use a resource: private, pod members, or the whole organization.',
        explainer: [
            'Every resource — agent, table, app, file — carries its own visibility. Private means only you; pod means everyone in this pod; organization means anyone in your org.',
            'This is about people. What an agent itself is allowed to touch is a separate thing — its access scope.',
        ],
        example:
            'Keep the salary table private to HR while the headcount app stays visible to the whole pod.',
        iconKind: 'auth-rbac',
        guideSlug: 'concepts/sharing',
        related: ['pod', 'table', 'scope'],
    },
    scope: {
        id: 'scope',
        term: 'Access scope',
        oneLiner:
            'The tables, folders, tools, and apps this worker is allowed to read and act on — nothing else.',
        explainer: [
            'Workers don’t get vague access to everything. You grant each one exactly the data and apps its job needs — like keys on a keyring.',
            'Who can see or edit the worker itself is separate: that’s sharing, behind the Share button.',
        ],
        example:
            'The triage agent reads the support folder and writes to the Tickets table — payroll doesn’t exist as far as it knows.',
        iconKind: 'auth-rbac',
        guideSlug: 'concepts/access-scope',
        related: ['agent', 'function', 'table', 'grant'],
    },
    approval: {
        id: 'approval',
        term: 'Approval',
        oneLiner:
            'A pause in a flow where a named person decides before work continues.',
        explainer: [
            'For steps with real consequences — sending money, emailing a customer, deleting records — add an approval. The flow stops, routes the decision to a person, and continues only after they approve.',
            'Approvals arrive in the app or in Slack, so deciding takes one click.',
        ],
        example:
            'The refund flow drafts everything automatically but waits for a manager’s click before money moves.',
        iconKind: 'workflows',
        guideSlug: 'concepts/approvals',
        related: ['flow', 'surface'],
    },
    variable: {
        id: 'variable',
        term: 'Variables',
        oneLiner:
            'The typed inputs an agent expects and the structured output it returns — its contract with the rest of the pod.',
        explainer: [
            'Variables turn an agent from freeform chat into a reusable building block. Declare the inputs it needs and the shape of what it hands back, and workflows, apps, and other agents can call it and rely on the result.',
            'Leave them empty for a conversational agent; define them the moment something downstream needs to read its output by field.',
        ],
        example:
            'A scoring agent takes { company, employees } and returns { score, reason } — so a workflow can branch on score without re-reading prose.',
        iconKind: 'agents',
        guideSlug: 'concepts/variables',
        related: ['agent', 'flow', 'function'],
    },
    runtime: {
        id: 'runtime',
        term: 'Runtime',
        oneLiner: 'The model and harness an agent runs on — pick per agent.',
        explainer: [
            'Different jobs want different brains. A heavyweight model for judgment-heavy work, a fast one for high-volume triage.',
            'Runtime is set per agent, so one pod can mix both.',
        ],
        example:
            'The contract-review agent runs the strongest model; the email-labeling agent runs the fast one.',
        iconKind: 'agents',
        guideSlug: 'concepts/runtimes',
        related: ['agent'],
    },
};

export function getConcept(id: ConceptId): ConceptEntry {
    return CONCEPTS[id];
}
