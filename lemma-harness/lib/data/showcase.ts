// Showcase data — examples of what teams have built with Lemma.
// These are inspirational examples, not real user data.

export interface ShowcaseItem {
    id: string;
    title: string;
    teamRole: string;          // e.g. "Customer Success Team"
    summary: string;           // One-line impact statement
    description: string;       // Full story
    builtWith: string[];       // Components used
    impact: string;            // Measurable outcome
    icon: 'support' | 'target' | 'approval' | 'content' | 'incident';
    accentColor: string;       // Gradient for card
}

export const SHOWCASE_ITEMS: ShowcaseItem[] = [
    {
        id: 'cs-ticket-triage',
        title: 'AI Ticket Triage & Response',
        teamRole: 'Customer Success',
        summary: 'AI reads new tickets, drafts responses, and routes complex issues — cutting first-response time by 70%.',
        description: 'The support team built a pod that connects to their helpapp via Slack. An AI agent reads each new ticket, searches the knowledge base for relevant docs, drafts a response, and either auto-sends simple answers or routes complex issues to the right specialist. A dashboard shows real-time queue status and SLA compliance.',
        builtWith: ['Agents', 'Knowledge Base', 'Automations', 'App Pages', 'Connected Apps'],
        impact: '70% faster first responses, 40% tickets auto-resolved',
        icon: 'support',
        accentColor: 'from-blue-500/15 to-cyan-500/15',
    },
    {
        id: 'sales-lead-research',
        title: 'Lead Research & Scoring',
        teamRole: 'Sales Team',
        summary: 'Incoming leads get auto-researched, scored, and routed to the right rep in under 5 minutes.',
        description: 'New leads from the website trigger an automation that enriches the lead record using an AI agent. The agent researches the company, estimates fit based on scoring criteria stored in a data table, and assigns the lead to the best-fit rep. Reps see a dashboard with their assigned leads and full research briefs.',
        builtWith: ['Agents', 'Data & Records', 'Automations', 'App Pages'],
        impact: '5x faster lead qualification, 30% more conversions',
        icon: 'target',
        accentColor: 'from-emerald-500/15 to-teal-500/15',
    },
    {
        id: 'ops-approval-workflows',
        title: 'Multi-Level Approval System',
        teamRole: 'Operations',
        summary: 'Purchase requests, vendor approvals, and budget exceptions flow through automated multi-step review.',
        description: 'The ops team replaced email-based approvals with a pod. Team members submit requests through an app page. An automation routes to the right approver based on amount, department, and type. AI pre-checks compliance against policy docs in the knowledge base. Approved requests auto-update finance records.',
        builtWith: ['Automations', 'App Pages', 'Data & Records', 'Knowledge Base', 'Agents'],
        impact: 'Approval time from 3 days to 4 hours',
        icon: 'approval',
        accentColor: 'from-amber-500/15 to-orange-500/15',
    },
    {
        id: 'marketing-content-ops',
        title: 'Content Operations Hub',
        teamRole: 'Marketing',
        summary: 'Content briefs, drafts, reviews, and publishing tracked in one place — with AI first drafts.',
        description: 'The marketing team built a content ops pod with a campaign tracker, AI brief-to-draft agent, review workflow, and published content archive. Writers submit topics, an AI agent generates a first draft based on brand guidelines in the knowledge base, and the piece moves through review stages on a Kanban-style app page.',
        builtWith: ['Agents', 'Knowledge Base', 'Automations', 'App Pages', 'Data & Records'],
        impact: 'Content output doubled, review cycles cut by 50%',
        icon: 'content',
        accentColor: 'from-purple-500/15 to-pink-500/15',
    },
    {
        id: 'eng-incident-response',
        title: 'Incident Response & Post-Mortems',
        teamRole: 'Engineering',
        summary: 'Incidents logged, escalated, and documented automatically — with AI-generated post-mortems.',
        description: 'When a Slack alert fires, an automation creates an incident record, notifies the on-call engineer, and starts a timeline. An AI agent monitors the thread and drafts a post-mortem when the incident is resolved. Past incident data and runbooks live in the knowledge base for faster resolution next time.',
        builtWith: ['Agents', 'Data & Records', 'Automations', 'Connected Apps', 'Knowledge Base'],
        impact: 'MTTR reduced by 45%, post-mortems completed same day',
        icon: 'incident',
        accentColor: 'from-red-500/15 to-orange-500/15',
    },
];
