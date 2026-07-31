import {
    buildKitAssistantInstructions,
    buildKitAssistantOpeningMessage,
    kitCatalog,
    type KitDefinition,
} from '@/lib/kits/catalog';

// A Recipe is anything you can add to a pod to upgrade it — on a spectrum of
// weight. A `prompt` recipe is the lightweight end: it seeds the assistant with
// an intent and lets it build (an app, an agent, or a bot on a surface). A
// `repo` recipe is the heavyweight end: a published kit installed from a source.
// Surfaces and connectors aren't a separate genre — almost every recipe wires
// them, so the agent establishes that operating context as part of building.

export type RecipeKind = 'prompt' | 'repo';
export type RecipeBuilds = 'app' | 'agent' | 'workflow' | 'surface' | 'pod';
export type RecipeOutput = RecipeBuilds | 'table' | 'files' | 'schedule';
export type RecipeCategory = 'app-shapes' | 'agent-channels' | 'operating-loops' | 'published';
export type RecipePreviewKind =
    | 'dashboard'
    | 'inbox'
    | 'knowledge'
    | 'portal'
    | 'whatsapp'
    | 'telegram'
    | 'slack'
    | 'email'
    | 'teams'
    | 'monitor'
    | 'triage'
    | 'approval'
    | 'briefing'
    | 'follow-up'
    | 'kit';
export type RecipePlatform = 'WHATSAPP' | 'TELEGRAM' | 'SLACK' | 'GMAIL' | 'OUTLOOK' | 'TEAMS';
export type StarterThemeId =
    | 'dashboards'
    | 'whatsapp'
    | 'telegram'
    | 'slack'
    | 'email'
    | 'teams'
    | 'knowledge'
    | 'intake'
    | 'automations';

export type RecipeSource =
    | { kind: 'prompt'; prompt: string }
    | { kind: 'repo'; github: string };

export interface Recipe {
    id: string;
    name: string;
    kicker: string;
    blurb: string;
    builds: RecipeBuilds;
    outputs: RecipeOutput[];
    category: RecipeCategory;
    preview: RecipePreviewKind;
    platforms?: RecipePlatform[];
    examples?: string[];
    featured?: boolean;
    highlights?: string[];
    source: RecipeSource;
}

// Accent keys map to existing semantic tokens via color-mix in CSS, so every
// accent adapts to light/dark automatically (see styles/features/resource-ledgers.css).
export type RecipeAccent = 'success' | 'info' | 'delight' | 'brand' | 'intelligence' | 'collaboration';

export interface RecipeCategoryMeta {
    id: RecipeCategory;
    label: string;
    blurb: string;
    accent: RecipeAccent;
    order: number;
}

export interface StarterTheme {
    id: StarterThemeId;
    name: string;
    kicker: string;
    description: string;
    preview: RecipePreviewKind;
    examples: string[];
    recipeIds: string[];
    promptExamples: Array<{
        recipeId: string;
        title: string;
        prompt: string;
    }>;
}

export const RECIPE_CATEGORIES: RecipeCategoryMeta[] = [
    { id: 'app-shapes', label: 'Build an app', blurb: 'Start from a recognizable product shape, then make it yours.', accent: 'brand', order: 1 },
    { id: 'agent-channels', label: 'Put an agent where work happens', blurb: 'Give this pod a useful presence in the channels people already use.', accent: 'collaboration', order: 2 },
    { id: 'operating-loops', label: 'Automate a loop', blurb: 'Set up work that keeps watching, routing, reviewing, or following up.', accent: 'intelligence', order: 3 },
    { id: 'published', label: 'Published kits', blurb: 'Install a complete, source-backed setup into this pod.', accent: 'success', order: 4 },
];

function starterPrompt(
    recipeId: string,
    title: string,
    build: string,
    askAbout: string,
    finishedVersion: string,
): StarterTheme['promptExamples'][number] {
    return {
        recipeId,
        title,
        prompt: [
            `Build ${build} inside this pod.`,
            'Before making changes, inspect the pod\'s existing apps, data, files, agents, workflows, connectors, and surfaces so you reuse what is already here.',
            `Ask the user for the missing details needed to shape it, especially ${askAbout}. Ask only one or two concise questions at a time, do not repeat anything already clear from the pod, and do not turn discovery into a long questionnaire.`,
            `Once you have enough context, build the smallest useful working version. ${finishedVersion}`,
            'Use believable sample data where it helps the user understand the result, show one realistic flow working end to end, and keep consequential external actions behind explicit approval.',
        ].join('\n\n'),
    };
}

// Themes are the front doors of the starter experience. A theme describes a
// product surface or family; the recipes inside it are the concrete builds.
export const STARTER_THEMES: StarterTheme[] = [
    {
        id: 'dashboards',
        name: 'Dashboards & internal tools',
        kicker: 'Build the place where the work gets run',
        description: 'Shape a focused operating screen around your data, decisions, and next actions.',
        preview: 'dashboard',
        examples: ['Operations', 'Pipeline', 'Project delivery'],
        recipeIds: ['dashboard-internal-tool', 'crm-pipeline-app', 'project-delivery-console'],
        promptExamples: [
            starterPrompt('dashboard-internal-tool', 'An operations dashboard shaped around your team\'s decisions', 'an operations dashboard and internal tool', 'the people using it, the decisions they make, source data, workflow states, permissions, and the actions the screen must support', 'Create the focused app, underlying data model, and useful agent behavior around those decisions rather than a generic KPI wall.'),
            starterPrompt('crm-pipeline-app', 'A CRM pipeline with owners, stages, and next actions', 'a CRM pipeline workspace', 'the relationship or revenue motion, pipeline stages, ownership, required account and contact data, next actions, and follow-up rules', 'Create the pipeline app, durable records, movement history, and agent-prepared research and follow-ups.'),
            starterPrompt('project-delivery-console', 'A project delivery tracker for blockers, risks, and decisions', 'a project delivery tracker', 'how work is planned and reviewed, project types, milestones, owners, blockers, risks, decisions, and the reporting cadence', 'Create a calm delivery view that foregrounds movement, blockers, and decisions, plus concise agent-written status summaries.'),
        ],
    },
    {
        id: 'whatsapp',
        name: 'WhatsApp agents',
        kicker: 'Serve, qualify, and coordinate in WhatsApp',
        description: 'Give this pod a WhatsApp presence with structured memory and a human handoff.',
        preview: 'whatsapp',
        examples: ['Customer support', 'Lead qualification', 'Field operations'],
        recipeIds: ['whatsapp-agent', 'whatsapp-support-desk', 'whatsapp-lead-qualifier', 'whatsapp-field-ops'],
        promptExamples: [
            starterPrompt('whatsapp-support-desk', 'A WhatsApp support agent with clean human handoff', 'a WhatsApp customer support agent with clean human handoff', 'the customers it serves, common requests, approved knowledge sources, tone, business hours, data to capture, escalation rules, and who takes over', 'Create the agent, support records, WhatsApp surface, and a human review and handoff path.'),
            starterPrompt('whatsapp-lead-qualifier', 'A WhatsApp agent that qualifies and routes inbound leads', 'a WhatsApp lead qualification agent', 'the ideal customer, qualification questions, disqualifiers, routing rules, ownership, CRM fields, response expectations, and when a salesperson should take over', 'Create the agent, lead records, WhatsApp surface, and a prepared handoff with the next sales action.'),
            starterPrompt('whatsapp-field-ops', 'A WhatsApp field-ops agent for updates, photos, and escalation', 'a WhatsApp field operations agent', 'who reports from the field, the jobs or locations involved, required updates and photos, validation rules, escalation conditions, and how coordinators review progress', 'Create the agent, structured field records, WhatsApp surface, and a clear operations review view.'),
        ],
    },
    {
        id: 'telegram',
        name: 'Telegram agents + mini apps',
        kicker: 'Combine a fast bot with a visual companion',
        description: 'Use Telegram for the conversation and a mini app for records, decisions, and review.',
        preview: 'telegram',
        examples: ['Community', 'Personal logging', 'Approvals'],
        recipeIds: ['telegram-agent-app', 'telegram-community-concierge', 'telegram-personal-logger', 'telegram-approval-bot'],
        promptExamples: [
            starterPrompt('telegram-community-concierge', 'A Telegram concierge for community questions and routing', 'a Telegram community concierge', 'the community, membership rules, common questions, trusted sources, moderation boundaries, escalation path, and what context should be retained', 'Create the Telegram agent, usable community memory, and a small companion view for reviewing activity and unresolved questions.'),
            starterPrompt('telegram-personal-logger', 'A Telegram capture bot with an organized personal logbook', 'a Telegram capture bot and personal logbook', 'what the user wants to capture, useful fields and tags, how entries should be organized, search and review habits, reminders, and privacy expectations', 'Create the Telegram bot, structured logbook, and a mini app for browsing, editing, and revisiting entries.'),
            starterPrompt('telegram-approval-bot', 'A Telegram approval agent with a focused mini app', 'a Telegram approval agent with a mini app', 'the requests being approved, submitters, required context, approvers, decision states, deadlines, delegation, notifications, and audit requirements', 'Create the Telegram agent, approval records and workflow, and a mini app that makes each decision quick and well informed.'),
        ],
    },
    {
        id: 'slack',
        name: 'Slack agents',
        kicker: 'Put a useful teammate in channels and DMs',
        description: 'Answer questions, coordinate incidents, and run recurring team rituals in Slack.',
        preview: 'slack',
        examples: ['Team knowledge', 'Incident response', 'Standup digest'],
        recipeIds: ['slack-agent', 'slack-knowledge-teammate', 'slack-incident-coordinator', 'slack-standup-digest'],
        promptExamples: [
            starterPrompt('slack-knowledge-teammate', 'A Slack teammate that answers from your team\'s knowledge', 'a Slack knowledge teammate grounded in this pod', 'which teams and channels it serves, trusted sources, answer boundaries, citation expectations, unanswered-question handling, permissions, and escalation ownership', 'Create the Slack agent and a source-grounded answer flow that links back to evidence and records knowledge gaps.'),
            starterPrompt('slack-incident-coordinator', 'A Slack incident coordinator with a live decision log', 'a Slack incident coordinator', 'incident types, severity rules, responders, channel conventions, required updates, decision capture, escalation paths, customer communication boundaries, and review expectations', 'Create the Slack agent, incident records and workflow, timeline and decision log, and a concise live status view.'),
            starterPrompt('slack-standup-digest', 'A Slack standup collector that publishes a useful digest', 'a Slack standup collector and digest', 'participating teams, collection cadence, prompts, time zones, blockers and risks to highlight, distribution channels, and what makes the digest useful', 'Create the Slack agent, scheduled collection loop, update records, and a compact digest with clear follow-ups.'),
        ],
    },
    {
        id: 'email',
        name: 'Email agents',
        kicker: 'Turn a mailbox into an operating queue',
        description: 'Triage incoming mail, prepare replies, and keep outbound work behind human review.',
        preview: 'email',
        examples: ['Support', 'Vendor requests', 'Executive inbox'],
        recipeIds: ['email-agent'],
        promptExamples: [
            starterPrompt('email-agent', 'An email agent that triages support and drafts replies', 'a support inbox agent with human-reviewed drafts', 'the mailbox, support categories, urgency rules, customer context, approved knowledge, ownership, service expectations, escalation rules, and reply approval policy', 'Create the email agent, triage and review queue, support records, and safe draft-reply workflow.'),
            starterPrompt('email-agent', 'An owned email queue for reviewing vendor requests', 'a vendor request email queue', 'the request types, required vendor information, reviewers, ownership, risk and urgency rules, decision states, response templates, and approval boundaries', 'Create the email agent, structured vendor requests, an owned review queue, and prepared responses that remain behind approval.'),
            starterPrompt('email-agent', 'A daily executive inbox briefing with decisions and follow-ups', 'an executive inbox briefing agent', 'the mailbox, priority people and topics, what can be summarized or ignored, sensitive-message handling, preferred briefing format, cadence, and follow-up boundaries', 'Create the email agent and a concise briefing that separates decisions, replies, follow-ups, and information while keeping all outbound actions under human control.'),
        ],
    },
    {
        id: 'teams',
        name: 'Microsoft Teams agents',
        kicker: 'Bring the pod into Teams chats and channels',
        description: 'Answer, capture, route, and escalate work without asking the team to leave Microsoft Teams.',
        preview: 'teams',
        examples: ['Policy Q&A', 'IT intake', 'Project updates'],
        recipeIds: ['teams-agent'],
        promptExamples: [
            starterPrompt('teams-agent', 'A Teams agent that answers policy questions with sources', 'a policy Q&A agent in Microsoft Teams', 'the audiences and Teams locations, authoritative policy sources, permission boundaries, citation requirements, uncertain-answer handling, and the policy owner escalation path', 'Create the Teams agent and a grounded answer experience that cites evidence and captures unanswered questions.'),
            starterPrompt('teams-agent', 'A Teams agent that captures and routes IT requests', 'an IT request intake agent in Microsoft Teams', 'request categories, required diagnostics, urgency and impact rules, routing and ownership, service expectations, escalation conditions, and status updates', 'Create the Teams agent, structured request records, triage workflow, and a clear queue for the IT team.'),
            starterPrompt('teams-agent', 'A Teams agent that publishes a useful project digest', 'a project update collector and digest in Microsoft Teams', 'the projects and participants, update cadence, required signals, blocker and risk rules, decision owners, summary audience, and delivery channel', 'Create the Teams agent, scheduled collection loop, durable updates, and an action-oriented digest.'),
        ],
    },
    {
        id: 'knowledge',
        name: 'Knowledge & research',
        kicker: 'Make the pod useful with what your team knows',
        description: 'Collect sources, find grounded answers, and turn knowledge into working material.',
        preview: 'knowledge',
        examples: ['Team handbook', 'Research library', 'Customer knowledge'],
        recipeIds: ['knowledge-workspace'],
        promptExamples: [
            starterPrompt('knowledge-workspace', 'A source-grounded team handbook people can actually use', 'a source-grounded team handbook', 'the audience, existing documents, key sections, ownership, permissions, update process, search needs, and which questions it must answer reliably', 'Create the knowledge structure, import or organize the sources, and provide grounded answers with links back to evidence.'),
            starterPrompt('knowledge-workspace', 'A research library that answers with links to evidence', 'a research library with evidence-backed answers', 'the research domain, source types, collection process, metadata, credibility rules, synthesis formats, permissions, and recurring research questions', 'Create the library, source organization, search and Q&A experience, and a workflow for turning evidence into useful briefs.'),
            starterPrompt('knowledge-workspace', 'A customer knowledge base that stays useful and searchable', 'a customer knowledge base', 'who uses it, customer and product scope, existing sources, permission boundaries, common questions, freshness requirements, ownership, and how gaps should be handled', 'Create the knowledge structure and a grounded answer experience that keeps customer context usable and points back to sources.'),
        ],
    },
    {
        id: 'intake',
        name: 'Intake & review',
        kicker: 'Give incoming work a clean front door',
        description: 'Capture requests, prepare context, route ownership, and make the next decision clear.',
        preview: 'triage',
        examples: ['Requests', 'Approvals', 'Human handoff'],
        recipeIds: ['portal-intake', 'inbox-review-queue', 'intake-triage', 'approval-review'],
        promptExamples: [
            starterPrompt('portal-intake', 'A client request portal with routing and clear status', 'a client request portal', 'the clients and request types, required information and files, authentication, routing, service expectations, status visibility, notifications, and ownership', 'Create the portal, request records, routing workflow, and a clear operator view with the right next actions.'),
            starterPrompt('inbox-review-queue', 'A review queue with prepared context and next actions', 'a queue where incoming work is prepared for human review', 'the incoming sources and item types, enrichment needed, urgency and ordering, reviewers, legal states, allowed actions, escalation rules, and approval boundaries', 'Create the queue, underlying records and workflow, and agent-prepared context and next actions for every item.'),
            starterPrompt('approval-review', 'An approval workflow with owners, evidence, and audit history', 'an approval workflow with clear context and audit history', 'the request types, submitters, required evidence, approvers and delegation, decision rules, deadlines, reminders, notifications, and audit requirements', 'Create the approval records, workflow, review experience, and durable decision history.'),
        ],
    },
    {
        id: 'automations',
        name: 'Monitors & operating loops',
        kicker: 'Keep useful work running in the background',
        description: 'Watch for change, prepare briefings, and follow up without losing human control.',
        preview: 'monitor',
        examples: ['Monitoring', 'Briefings', 'Follow-up'],
        recipeIds: ['monitor-alert', 'scheduled-briefing', 'follow-up-chaser'],
        promptExamples: [
            starterPrompt('monitor-alert', 'A monitor that detects changes and alerts the right person', 'a monitor that detects important changes and alerts the right person', 'the sources to watch, meaningful events, thresholds, exclusions, check frequency, recipients, severity, escalation, and duplicate-alert handling', 'Create the monitor, durable event log, alert workflow, and a clear review path for what changed and why it matters.'),
            starterPrompt('scheduled-briefing', 'A scheduled morning briefing built from the right sources', 'a useful scheduled morning briefing', 'the audience, decisions it supports, source systems, metrics and exceptions, preferred structure and tone, delivery time and surface, ownership, and follow-up expectations', 'Create the source collection, scheduled workflow, and concise agent-written briefing with links to the underlying evidence.'),
            starterPrompt('follow-up-chaser', 'A follow-up loop for stalled commitments and next messages', 'a follow-up chaser for stalled commitments', 'the commitments or relationships to track, owners, promises, due dates, stale rules, priority, follow-up tone, approval boundaries, escalation, and delivery channel', 'Create the tracking records, scheduled checks, and agent-prepared follow-ups while keeping external messages behind explicit approval.'),
        ],
    },
];

export const FEATURED_STARTER_THEMES = STARTER_THEMES.slice(0, 5);

const CATEGORY_ACCENT: Record<RecipeCategory, RecipeAccent> = RECIPE_CATEGORIES.reduce(
    (acc, meta) => ({ ...acc, [meta.id]: meta.accent }),
    {} as Record<RecipeCategory, RecipeAccent>,
);

export function getRecipeAccent(recipe: Recipe): RecipeAccent {
    return CATEGORY_ACCENT[recipe.category] ?? 'intelligence';
}

export const RECIPE_BUILDS_LABEL: Record<RecipeBuilds, string> = {
    app: 'Builds an app',
    agent: 'Builds an agent',
    workflow: 'Builds a workflow',
    surface: 'Sets up a bot',
    pod: 'Sets up the pod',
};

export const RECIPE_OUTPUT_LABEL: Record<RecipeOutput, string> = {
    app: 'App',
    agent: 'Agent',
    workflow: 'Workflow',
    surface: 'Surface',
    pod: 'Pod setup',
    table: 'Data',
    files: 'Knowledge',
    schedule: 'Schedule',
};

const SEED = 'Seed a few believable sample rows so it feels alive and is testable the moment it opens.';

const PROMPT_RECIPES: Recipe[] = [
    // ── Recognizable app shapes ───────────────────────────────────
    {
        id: 'dashboard-internal-tool', name: 'Custom operations dashboard', kicker: 'Shape it around the decisions your team makes.',
        category: 'app-shapes', builds: 'app', outputs: ['app', 'table', 'agent'], preview: 'dashboard', featured: true,
        blurb: 'A purpose-built operating screen with live data, clear actions, and an agent keeping it current.',
        examples: ['Sales pipeline', 'Project operations', 'Renewal review'],
        highlights: ['A working app shaped around the decisions you make', 'Structured data behind every view and action', 'An agent that updates, drafts, and flags what needs attention'],
        source: { kind: 'prompt', prompt: `Build a dashboard and internal tool for this pod.\nStart by asking what work the user needs to see and operate, then build a focused app, the tables behind it, and an agent that keeps the view useful. Avoid a generic KPI wall: design around the decisions, transitions, and next actions in this specific workflow.\n${SEED}\nOpen the finished app and show one realistic action working end to end.` },
    },
    {
        id: 'crm-pipeline-app', name: 'Customer pipeline console', kicker: 'Accounts, opportunities, and next actions in one operating view.',
        category: 'app-shapes', builds: 'app', outputs: ['app', 'table', 'agent', 'workflow'], preview: 'dashboard',
        blurb: 'A working relationship and pipeline system with clear ownership, movement, and agent-prepared follow-up.',
        examples: ['Sales pipeline', 'Partnerships', 'Customer success'],
        highlights: ['A customer and opportunity data model', 'Pipeline views built around movement and ownership', 'Agent-prepared research and next actions'],
        source: { kind: 'prompt', prompt: `Build a customer pipeline console for this pod.\nAsk what relationship or revenue motion the team runs, then create the account, contact, opportunity, and activity data it actually needs. Build a focused app for moving work through the pipeline and an agent that prepares research and next actions without sending anything externally.\n${SEED}\nDemonstrate one opportunity moving forward with its history intact.` },
    },
    {
        id: 'project-delivery-console', name: 'Project delivery console', kicker: 'Plans, owners, risks, and decisions without the project-management sprawl.',
        category: 'app-shapes', builds: 'app', outputs: ['app', 'table', 'agent', 'workflow'], preview: 'dashboard',
        blurb: 'A calm delivery workspace that shows what is moving, what is blocked, and what needs a decision.',
        examples: ['Client delivery', 'Product launches', 'Operations projects'],
        highlights: ['Projects, milestones, owners, and risks', 'A decision-oriented delivery view', 'Agent-written status and blocker summaries'],
        source: { kind: 'prompt', prompt: `Build a project delivery console for this pod.\nAsk how the team plans and reviews delivery, then create the smallest useful model for projects, milestones, owners, risks, and decisions. Build a calm app that foregrounds movement and blockers, plus an agent that prepares status summaries from the underlying work.\n${SEED}\nShow one project review from signal to decision.` },
    },
    {
        id: 'inbox-review-queue', name: 'Inbox & review queue', kicker: 'Everything that needs a human, already prepared.',
        category: 'app-shapes', builds: 'app', outputs: ['app', 'table', 'agent', 'workflow'], preview: 'inbox',
        blurb: 'Incoming work arrives sorted, enriched, and ready to approve, reply, assign, or escalate.',
        examples: ['Support inbox', 'Lead review', 'Content approvals'],
        highlights: ['A calm queue ordered by urgency and status', 'Agent-drafted context and next actions on every item', 'Clear approve, reply, assign, and escalate controls'],
        source: { kind: 'prompt', prompt: `Build an inbox and review queue app for this pod.\nCreate a focused operator experience where incoming items arrive with urgency, context, an owner, and an agent-prepared next action. Include the workflow and data needed to move items through clear legal states. Keep consequential actions behind human approval.\n${SEED}\nShow the user the first prepared item and how it moves through the queue.` },
    },
    {
        id: 'knowledge-workspace', name: 'Knowledge workspace', kicker: 'Docs, files, and an agent that can actually use them.',
        category: 'app-shapes', builds: 'app', outputs: ['app', 'files', 'agent'], preview: 'knowledge',
        blurb: 'A shared place to collect knowledge, find answers, and turn what the team knows into useful work.',
        examples: ['Team handbook', 'Research library', 'Customer knowledge'],
        highlights: ['A browsable home for the pod’s docs and files', 'Source-grounded answers with links back to evidence', 'A structure that stays editable as the knowledge grows'],
        source: { kind: 'prompt', prompt: `Build a knowledge workspace for this pod.\nCreate a calm app for browsing the pod's important files and documents, plus an agent that answers from those sources and links back to evidence. Establish a small, believable information structure and seed example knowledge so search and Q&A work immediately.\nKeep the experience closer to a focused knowledge product than a dashboard.` },
    },
    {
        id: 'portal-intake', name: 'Portal & intake', kicker: 'A clean front door for requests, submissions, and status.',
        category: 'app-shapes', builds: 'app', outputs: ['app', 'table', 'workflow', 'agent'], preview: 'portal',
        blurb: 'Collect structured requests, route them to the right people, and give submitters a clear next step.',
        examples: ['Client requests', 'Hiring intake', 'Service desk'],
        highlights: ['A focused submission experience with the right fields', 'Automatic validation, enrichment, and routing', 'A review view for the people handling each request'],
        source: { kind: 'prompt', prompt: `Build a portal and intake app for this pod.\nCreate a polished submission experience, the table that stores requests, and a workflow that validates, enriches, and routes each one. Add an internal review view and use an agent only where drafting or classification genuinely helps.\n${SEED}\nDemonstrate one submission arriving and being routed.` },
    },

    // ── Agents in the channels people already use ─────────────────
    {
        id: 'whatsapp-agent', name: 'Custom WhatsApp agent', kicker: 'Define the job; keep the conversation and handoff in one pod.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'app', 'table'], preview: 'whatsapp', platforms: ['WHATSAPP'], featured: true,
        blurb: 'A useful WhatsApp agent for customers, plus an inbox where your team reviews context and takes over.',
        examples: ['Customer support', 'Lead capture', 'Field updates'],
        highlights: ['A WhatsApp identity connected to a focused agent', 'A companion inbox for history, escalation, and handoff', 'Structured records created from every useful conversation'],
        source: { kind: 'prompt', prompt: `Set up a WhatsApp agent and companion operator inbox for this pod.\nAsk what the agent should handle, then create the agent, the WhatsApp surface, a table for the important records, and a small app where a human can review conversations, see context, and take over. Use Lemma's managed identity or help connect the user's account, and confirm before external actions.\n${SEED}\nFinish with one safe test conversation and show it in the inbox.` },
    },
    {
        id: 'whatsapp-support-desk', name: 'WhatsApp support & handoff', kicker: 'Resolve the routine; prepare the rest for a person.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'app', 'table'], preview: 'whatsapp', platforms: ['WHATSAPP'],
        blurb: 'A customer support agent with grounded answers, case history, urgency, and a clean human takeover path.',
        examples: ['Order questions', 'Service requests', 'Customer care'],
        highlights: ['Grounded answers from approved knowledge', 'A durable customer and case history', 'Urgent and uncertain conversations routed to people'],
        source: { kind: 'prompt', prompt: `Build a WhatsApp customer support desk for this pod.\nCreate a support agent grounded in the pod's approved knowledge, a WhatsApp surface, customer and case records, and an operator inbox for context, urgency, and human takeover. The agent may answer routine questions but must escalate uncertainty and confirm before any consequential action.\n${SEED}\nRun a safe test that demonstrates both resolution and handoff.` },
    },
    {
        id: 'whatsapp-lead-qualifier', name: 'WhatsApp lead qualifier', kicker: 'Respond quickly, learn what matters, and hand over a prepared lead.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'table', 'workflow'], preview: 'whatsapp', platforms: ['WHATSAPP'],
        blurb: 'A conversational front door that captures intent, qualifies fit, and prepares the next sales action.',
        examples: ['Inbound enquiries', 'Property leads', 'Service sales'],
        highlights: ['Natural qualification around the team’s real criteria', 'Structured lead and conversation records', 'A clear handoff with context and suggested next step'],
        source: { kind: 'prompt', prompt: `Build a WhatsApp lead qualification agent for this pod.\nAsk how the team defines fit and what a useful handoff contains, then create the agent, WhatsApp surface, lead data, and routing workflow. Keep the conversation natural, capture only useful information, and never invent pricing or commitments.\n${SEED}\nDemonstrate one lead moving from first message to a prepared handoff.` },
    },
    {
        id: 'whatsapp-field-ops', name: 'WhatsApp field operations', kicker: 'Let people report work from the field without learning another system.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'app', 'table'], preview: 'whatsapp', platforms: ['WHATSAPP'],
        blurb: 'Capture updates, photos, issues, and confirmations in WhatsApp while operations sees a structured live view.',
        examples: ['Site updates', 'Service visits', 'Delivery checks'],
        highlights: ['Guided field updates through chat', 'Structured jobs, evidence, and exceptions', 'An operations view for review and escalation'],
        source: { kind: 'prompt', prompt: `Build a WhatsApp field operations system for this pod.\nAsk what field event or job people need to report, then create the WhatsApp agent, structured records, evidence handling, and an operations app for progress, exceptions, and review. Keep the chat flow short and usable on the move.\n${SEED}\nDemonstrate one field update appearing in the operations view.` },
    },
    {
        id: 'telegram-agent-app', name: 'Custom Telegram bot + mini app', kicker: 'Define the bot and the visual companion it should open.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'app', 'table'], preview: 'telegram', platforms: ['TELEGRAM'], featured: true,
        blurb: 'A Telegram bot for fast input and answers, paired with an app for records, trends, and human review.',
        examples: ['Personal logging', 'Team intake', 'Ask my data'],
        highlights: ['A Telegram bot people can use immediately', 'A companion app for records, trends, and review', 'One shared data model across chat and the app'],
        source: { kind: 'prompt', prompt: `Set up a Telegram agent with a companion app for this pod.\nAsk what people should message the bot, then create the agent, Telegram surface, structured data, and a small app for browsing records, seeing useful summaries, and reviewing anything the agent could not resolve. Use Lemma's bot or help connect the user's own bot, and confirm before external actions.\n${SEED}\nFinish by showing a test Telegram interaction reflected inside the app.` },
    },
    {
        id: 'telegram-community-concierge', name: 'Telegram community concierge', kicker: 'Welcome, answer, route, and keep the community context usable.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'app', 'table'], preview: 'telegram', platforms: ['TELEGRAM'],
        blurb: 'A community bot for grounded answers and intake, with a companion app for topics, people, and unresolved needs.',
        examples: ['Member community', 'Cohort support', 'Customer group'],
        highlights: ['A helpful bot for DMs and selected groups', 'Source-grounded answers and topic capture', 'A companion view for unresolved questions and member needs'],
        source: { kind: 'prompt', prompt: `Build a Telegram community concierge for this pod.\nCreate a bot grounded in approved community knowledge, route the right groups and DMs, capture useful topics and unresolved needs, and build a companion app for human review. Avoid noisy proactive posting and confirm before inviting or messaging people.\n${SEED}\nShow one answered question and one escalated need.` },
    },
    {
        id: 'telegram-personal-logger', name: 'Telegram capture & logbook', kicker: 'Message the bot in seconds; organize and revisit it in the mini app.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'app', 'table'], preview: 'telegram', platforms: ['TELEGRAM'],
        blurb: 'A fast personal or team capture bot that turns messages into structured, searchable records.',
        examples: ['Research notes', 'Expenses', 'Work log'],
        highlights: ['Low-friction capture through Telegram', 'Agent-assisted extraction into useful fields', 'A mini app for search, trends, and correction'],
        source: { kind: 'prompt', prompt: `Build a Telegram capture and logbook system for this pod.\nAsk what people need to log, then create the bot, structured data, extraction agent, and a mini app for browsing, searching, correcting, and summarizing entries. Accept text and useful attachments without making the chat flow heavy.\n${SEED}\nDemonstrate one message becoming an editable record.` },
    },
    {
        id: 'telegram-approval-bot', name: 'Telegram approval bot', kicker: 'Bring a prepared decision to the right person, wherever they are.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'workflow', 'app'], preview: 'telegram', platforms: ['TELEGRAM'],
        blurb: 'A decision bot with evidence, thresholds, and a mini app for queues, audit history, and deeper review.',
        examples: ['Spend approval', 'Exceptions', 'Content review'],
        highlights: ['Concise decision requests in Telegram', 'Explicit approve, reject, revise, and escalate paths', 'A durable queue and audit history in the companion app'],
        source: { kind: 'prompt', prompt: `Build a Telegram approval bot with a companion app for this pod.\nAsk what decisions need approval and which evidence and thresholds matter, then create the workflow, Telegram surface, approval agent, and mini app for queues and audit history. Every consequential action must wait for an explicit human decision.\n${SEED}\nDemonstrate one safe approval from request through recorded decision.` },
    },
    {
        id: 'slack-agent', name: 'Custom Slack agent', kicker: 'Give a focused job to a teammate in channels and DMs.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'workflow', 'schedule'], preview: 'slack', platforms: ['SLACK'], featured: true,
        blurb: 'Give the pod a focused Slack presence that answers, routes work, and posts useful updates.',
        examples: ['Team Q&A', 'Standup digest', 'Operations assistant'],
        highlights: ['A Slack agent for DMs and selected channels', 'Channel-aware routing and mention behaviour', 'Optional scheduled digests and proactive updates'],
        source: { kind: 'prompt', prompt: `Set up a Slack agent for this pod.\nAsk what job it should own, then create the agent, connect the Slack surface, route the relevant channels, and add a scheduled digest or proactive update only when it helps the use case. Seed believable context so the first DM or mention produces a useful answer.\nConfirm before connecting accounts, posting messages, or inviting people.` },
    },
    {
        id: 'slack-knowledge-teammate', name: 'Slack knowledge teammate', kicker: 'Answer in the flow of work and point back to the source.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'files'], preview: 'slack', platforms: ['SLACK'],
        blurb: 'A channel-aware teammate that answers from pod knowledge and escalates when the evidence is thin.',
        examples: ['Policy Q&A', 'Product knowledge', 'Team handbook'],
        highlights: ['Grounded answers in DMs and selected channels', 'Links back to source evidence', 'A clear uncertainty and escalation policy'],
        source: { kind: 'prompt', prompt: `Build a Slack knowledge teammate for this pod.\nGround the agent in the pod's approved files and docs, connect it to the relevant Slack DMs and channels, and require answers to point back to source evidence. Define when it should stay quiet, express uncertainty, or route a question to a person.\nSeed enough believable knowledge to run a safe first test.` },
    },
    {
        id: 'slack-incident-coordinator', name: 'Slack incident coordinator', kicker: 'Capture the signal, organize the response, and keep the channel current.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'workflow', 'table'], preview: 'slack', platforms: ['SLACK'],
        blurb: 'An incident-room agent that structures reports, tracks owners and decisions, and prepares status updates.',
        examples: ['Service incidents', 'Customer escalations', 'Operations exceptions'],
        highlights: ['Structured incident intake from Slack', 'Owners, decisions, and timeline captured durably', 'Prepared status updates held for human review'],
        source: { kind: 'prompt', prompt: `Build a Slack incident coordinator for this pod.\nAsk what counts as an incident and how response is run, then create the agent, Slack routing, incident records, and workflow for severity, owners, decisions, and status. It may prepare updates but must confirm before posting broadly or closing an incident.\n${SEED}\nDemonstrate one test incident from report to prepared update.` },
    },
    {
        id: 'slack-standup-digest', name: 'Slack standup & digest', kicker: 'Collect updates with less ceremony and publish a useful team picture.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'workflow', 'schedule'], preview: 'slack', platforms: ['SLACK'],
        blurb: 'A scheduled team ritual that gathers focused updates, spots blockers, and prepares a concise digest.',
        examples: ['Daily standup', 'Weekly operations', 'Project pulse'],
        highlights: ['Scheduled prompts in the right place', 'A concise digest organized around movement and blockers', 'Human review before broad or external posting'],
        source: { kind: 'prompt', prompt: `Build a Slack standup and digest loop for this pod.\nAsk who the ritual serves, what questions matter, and the right cadence, then create the schedule, Slack surface, response workflow, and agent-written digest. Optimize for a useful team picture rather than repetitive status prose. Confirm before sending prompts or publishing the digest.\n${SEED}` },
    },
    {
        id: 'email-agent', name: 'Gmail or Outlook agent', kicker: 'Turn a mailbox into a calm, reviewable work queue.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'app', 'workflow'], preview: 'email', platforms: ['GMAIL', 'OUTLOOK'],
        blurb: 'Classify incoming mail, prepare replies, and bring only the decisions that need a person into one queue.',
        examples: ['Support triage', 'Vendor requests', 'Executive inbox'],
        highlights: ['A connected Gmail or Outlook mailbox with safe filters', 'Agent-drafted replies held for review', 'A queue for urgency, ownership, and escalation'],
        source: { kind: 'prompt', prompt: `Set up an email agent for this pod using Gmail or Outlook.\nAsk which mailbox and messages matter, then create the agent, connect the email surface with safe sender filters, store the useful work, and build a small review queue for drafted replies, urgency, ownership, and escalation. Keep every outbound reply behind explicit approval.\n${SEED}` },
    },
    {
        id: 'teams-agent', name: 'Microsoft Teams agent', kicker: 'Bring the pod into Teams chats and channels.',
        category: 'agent-channels', builds: 'surface', outputs: ['agent', 'surface', 'workflow'], preview: 'teams', platforms: ['TEAMS'],
        blurb: 'A Teams-native agent for answers, routed requests, and useful operational updates.',
        examples: ['Policy Q&A', 'IT intake', 'Project updates'],
        highlights: ['An agent available in Teams DMs and selected channels', 'Channel routing to the right pod capability', 'Clear escalation when a person needs to step in'],
        source: { kind: 'prompt', prompt: `Set up a Microsoft Teams agent for this pod.\nAsk what job it should own, then create the agent, connect the Teams surface, route relevant channels, and add only the workflow needed to answer, capture, or escalate that work. Seed believable context and confirm before connecting accounts or sending external messages.` },
    },

    // ── Durable operating loops ───────────────────────────────────
    {
        id: 'monitor-alert', name: 'Monitor & alert', kicker: 'Keep watching. Speak up only when something matters.',
        category: 'operating-loops', builds: 'workflow', outputs: ['workflow', 'schedule', 'table', 'agent'], preview: 'monitor',
        blurb: 'Watch pages, records, prices, or signals on a schedule and surface only meaningful changes.',
        examples: ['Competitor changes', 'Pricing watch', 'Account risk'],
        highlights: ['A scheduled watcher with a durable change history', 'Rules that separate meaningful movement from noise', 'A concise alert delivered where the team works'],
        source: { kind: 'prompt', prompt: `Build a monitor-and-alert loop for this pod.\nAsk what should be watched and what counts as meaningful, then create the data history, checking workflow, schedule, and concise agent-written alert. Deliver alerts through the most appropriate existing surface and confirm before adding any external connection.\n${SEED}` },
    },
    {
        id: 'intake-triage', name: 'Intake & triage', kicker: 'Turn messy incoming work into a clean, owned queue.',
        category: 'operating-loops', builds: 'workflow', outputs: ['workflow', 'table', 'agent', 'app'], preview: 'triage',
        blurb: 'Capture requests from the right source, classify them, assign ownership, and prepare the next action.',
        examples: ['Support requests', 'Sales leads', 'Internal requests'],
        highlights: ['One structured queue across the chosen intake source', 'Automatic urgency, classification, and ownership suggestions', 'A human-ready next action on every item'],
        source: { kind: 'prompt', prompt: `Build an intake-and-triage loop for this pod.\nAsk where requests arrive and how they should be owned, then create the structured queue, agent classification, routing workflow, and a focused app for the people handling the work. Preserve human control over irreversible or outbound actions.\n${SEED}` },
    },
    {
        id: 'approval-review', name: 'Approvals & reviews', kicker: 'Bring the context. Let a person make the call.',
        category: 'operating-loops', builds: 'app', outputs: ['app', 'workflow', 'table', 'agent'], preview: 'approval',
        blurb: 'A review queue with thresholds, evidence, and one clear decision for every item.',
        examples: ['Spend approval', 'Content review', 'Customer exceptions'],
        highlights: ['A focused queue of decisions waiting on people', 'Evidence, thresholds, and an agent-prepared recommendation', 'Explicit approve, reject, revise, and escalate paths'],
        source: { kind: 'prompt', prompt: `Build an approvals-and-reviews system for this pod.\nAsk what requires approval and which thresholds matter, then create the queue app, underlying data, workflow states, and agent-prepared context for each decision. Every consequential action must wait for an explicit human choice.\n${SEED}` },
    },
    {
        id: 'scheduled-briefing', name: 'Scheduled briefing', kicker: 'A useful update, assembled and delivered on time.',
        category: 'operating-loops', builds: 'workflow', outputs: ['workflow', 'schedule', 'agent', 'surface'], preview: 'briefing',
        blurb: 'Collect the right signals, write a concise briefing, and deliver it on a reliable cadence.',
        examples: ['Daily operations', 'Weekly leadership', 'Customer health'],
        highlights: ['A scheduled workflow gathering the right source material', 'A concise briefing written for its actual audience', 'Delivery through Slack, Teams, email, or another active surface'],
        source: { kind: 'prompt', prompt: `Build a scheduled briefing loop for this pod.\nAsk what the audience needs to know and on what cadence, then create the source collection, scheduled workflow, and agent-written briefing. Deliver it through an existing surface when available, or offer to connect the best one with approval. Seed a believable first briefing so the format is immediately visible.` },
    },
    {
        id: 'follow-up-chaser', name: 'Follow-up & chase', kicker: 'Keep promises, deadlines, and relationships from going quiet.',
        category: 'operating-loops', builds: 'workflow', outputs: ['workflow', 'schedule', 'table', 'agent'], preview: 'follow-up',
        blurb: 'Track what is waiting, notice what has stalled, and prepare the next follow-up for approval.',
        examples: ['Sales follow-ups', 'Invoice reminders', 'Project commitments'],
        highlights: ['A durable record of owners, promises, and next actions', 'Scheduled checks for anything becoming stale', 'A prepared follow-up that stays behind human approval'],
        source: { kind: 'prompt', prompt: `Build a follow-up-and-chase loop for this pod.\nAsk what commitments or relationships must not go quiet, then create the tracking data, scheduled stale-item check, and agent-prepared follow-up. Keep external messages behind explicit approval and show the user which item needs attention first.\n${SEED}` },
    },
];

function kitToRecipe(kit: KitDefinition): Recipe {
    return {
        id: kit.id,
        name: kit.name,
        kicker: 'A complete setup from a published source.',
        blurb: kit.description,
        builds: 'pod',
        outputs: ['pod'],
        category: 'published',
        preview: 'kit',
        source: { kind: 'repo', github: kit.github },
    };
}

export const recipeCatalog: Recipe[] = [
    ...PROMPT_RECIPES,
    ...kitCatalog.map(kitToRecipe),
];

export const appRecipes: Recipe[] = recipeCatalog.filter((recipe) => recipe.builds === 'app');
export const featuredRecipes: Recipe[] = recipeCatalog.filter((recipe) => recipe.featured);

export function getStarterTheme(id: string | null | undefined): StarterTheme | null {
    if (!id) return null;
    return STARTER_THEMES.find((theme) => theme.id === id) ?? null;
}

export function recipesForTheme(theme: StarterTheme): Recipe[] {
    const recipeById = new Map(recipeCatalog.map((recipe) => [recipe.id, recipe]));
    return theme.recipeIds.flatMap((recipeId) => {
        const recipe = recipeById.get(recipeId);
        return recipe ? [recipe] : [];
    });
}

export function getPrimaryThemeForRecipe(recipe: Recipe): StarterTheme | null {
    return STARTER_THEMES.find((theme) => theme.recipeIds.includes(recipe.id)) ?? null;
}

export function getRecipeById(id: string | null | undefined): Recipe | null {
    if (!id) return null;
    return recipeCatalog.find((recipe) => recipe.id === id) ?? null;
}

export function recipesByCategory(category: RecipeCategory): Recipe[] {
    return recipeCatalog.filter((recipe) => recipe.category === category);
}

// Concrete "what you'll get" points for the detail page. Per-recipe overrides
// win; otherwise we derive an honest trio from what the recipe builds.
export function getRecipeHighlights(recipe: Recipe): string[] {
    if (recipe.highlights?.length) return recipe.highlights;

    switch (recipe.builds) {
        case 'surface':
            return [
                'A bot people message — nothing new to open',
                'An agent responds, stores, and acts; you approve anything outbound',
                'You pick the channel and who’s involved — the assistant connects it',
            ];
        case 'workflow':
            return [
                'A workflow that runs on a schedule, on its own',
                'Flags only what changed, so you look when it matters',
                'Seeded so you can see it work right away',
            ];
        case 'pod':
            return [
                'A full set of agents, data, and setup installed together',
                'Customizable before anything is created',
                'Everything stays editable and exportable',
            ];
        default:
            return [
                'An app you open to do the work',
                'An agent drafts and flags; a human decides',
                'Seeded with sample data so it’s usable immediately',
            ];
    }
}

// Short prompt strings for the lightweight chip UI in the "New app" modal.
export function getAppRecipeExamples(limit = 4): string[] {
    return appRecipes
        .slice(0, limit)
        .map((recipe) => (recipe.source.kind === 'prompt' ? recipe.source.prompt.split('\n')[0] : recipe.name));
}

// ── Launch helpers ────────────────────────────────────────────────

export type RecipeMode = 'install' | 'customize';

// A repo recipe is just a kit under the hood — rebuild the KitDefinition so the
// existing README + assistant-install helpers keep working.
export function recipeToKit(recipe: Recipe): KitDefinition | null {
    if (recipe.source.kind !== 'repo') return null;
    return { id: recipe.id, name: recipe.name, description: recipe.blurb, github: recipe.source.github };
}

export interface RecipeLaunch {
    message: string;
    instructions: string;
    metadata: Record<string, unknown>;
}

function buildRecipePromptInstructions(recipe: Recipe): string {
    const resource = recipe.builds === 'app'
        ? 'app'
        : recipe.builds === 'agent'
            ? 'agent'
            : recipe.builds === 'workflow'
                ? 'workflow'
                : recipe.builds === 'surface'
                    ? 'agent reachable through a surface (a bot people message)'
                    : 'set of pod resources';

    const lines = [
        `You are helping build the "${recipe.name}" recipe as a Lemma ${resource} in the current pod.`,
        `The coherent first version is expected to include: ${recipe.outputs.map((output) => RECIPE_OUTPUT_LABEL[output]).join(', ')}.`,
        ...(recipe.platforms?.length ? [`The intended external surface is: ${recipe.platforms.join(' or ')}.`] : []),
        'Use the user-visible message as the product intent. Do not repeat these hidden instructions back to the user.',
        'Inspect relevant pod context and existing resources before creating anything; reuse what already fits.',
        'Build the smallest useful first version. Keep it minimal, calm, and operational; avoid generic dashboard chrome.',
        'As part of setup, establish the operating context for THIS use case. Tailor what you ask to the recipe and ask only what is needed — one or two friendly questions at a time, never blocking on anything not required for a useful first version:',
        '- Who works on this with them, so you can invite those people to the workspace.',
        '- Where this work actually happens and which tools or inboxes are involved (for example Gmail or Outlook for mail, Slack for chat, a website). Offer to initiate the connection yourself and proceed only once they approve.',
        '- Wire the surfaces and connectors that fit the use case so the result plugs into how they already work.',
    ];

    if (recipe.builds === 'surface') {
        lines.push('This recipe is reached as a bot people message: create the agent, connect the surface it runs on, and confirm before any external action.');
    }

    lines.push('After it is built, summarize what was created, what was connected, and who was invited; display or link the resource.');
    return lines.join('\n');
}

// The user's very first build in Lemma. Threaded into the hidden instructions so
// the assistant treats it as a first impression — show capability, move fast, and
// make it feel like magic rather than setup homework.
export const FIRST_RUN_DELIGHT = [
    'This is the very first thing this person is building in Lemma — their first impression of the product. Make it feel like magic, not setup.',
    'Open with a warm, genuine one-line greeting that welcomes them to Lemma and makes them feel they picked something special — confident and personal, never corporate or gushing. Then get straight to building.',
    'Lead with momentum: build a working first version fast and seed it with believable sample data so it is alive the moment it opens. Do not make them configure things before they see something work.',
    'Wire the surface or connector that makes it feel connected to their real life — a bot they message, an inbox, a channel — and offer to connect it for them.',
    'Ask at most one short question, and only if you genuinely cannot proceed without it. Never block the wow on setup.',
    'Narrate warmly and briefly as you go, and slip in one small delightful touch they did not ask for.',
    'Finish by showing the working result and one concrete thing they can try right now. Keep it calm and confident — no walls of text.',
].join('\n');

// The message + hidden instructions + metadata used to open a full conversation
// for a recipe (prompt recipes seed an intent; repo recipes seed the kit install).
export interface RecipeLaunchOptions {
    podName?: string | null;
    mode?: RecipeMode;
    firstRun?: boolean;
    message?: string | null;
}

export function getRecipeLaunch(recipe: Recipe, opts?: RecipeLaunchOptions): RecipeLaunch {
    const launch: RecipeLaunch = recipe.source.kind === 'repo'
        ? (() => {
            const kit = recipeToKit(recipe) as KitDefinition;
            const mode: RecipeMode = opts?.mode ?? 'install';
            return {
                message: buildKitAssistantOpeningMessage(kit, mode),
                instructions: buildKitAssistantInstructions(kit, mode, opts?.podName),
                metadata: {
                    source: 'recipe',
                    recipe_id: recipe.id,
                    recipe_kind: 'repo',
                    github: (recipe.source as { github: string }).github,
                    install_mode: mode,
                    builds: recipe.builds,
                    outputs: recipe.outputs,
                    category: recipe.category,
                },
            };
        })()
        : {
            message: opts?.message?.trim() || recipe.source.prompt,
            instructions: buildRecipePromptInstructions(recipe),
            metadata: {
                source: 'recipe',
                recipe_id: recipe.id,
                recipe_kind: 'prompt',
                intent: 'create_resource',
                resource_type: recipe.builds,
                outputs: recipe.outputs,
                category: recipe.category,
                platforms: recipe.platforms || [],
            },
        };

    if (opts?.firstRun) {
        return {
            ...launch,
            instructions: `${FIRST_RUN_DELIGHT}\n\n${launch.instructions}`,
            metadata: { ...launch.metadata, first_run: true },
        };
    }
    return launch;
}

// Opening a recipe always lands in the full conversation view, not a background chat.
export function buildRecipeConversationHref(podId: string, recipe: Recipe, opts?: RecipeLaunchOptions): string {
    const launch = getRecipeLaunch(recipe, opts);
    const params = new URLSearchParams();
    params.set('assistantMessage', launch.message);
    params.set('conversationInstructions', launch.instructions);
    params.set('conversationMetadata', JSON.stringify(launch.metadata));
    return `/pod/${podId}/conversations/new?${params.toString()}`;
}
