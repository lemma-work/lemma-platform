import {
  Bot,
  Braces,
  CalendarClock,
  Code2,
  Cpu,
  Database,
  FileText,
  FolderOpen,
  GitBranch,
  LockKeyhole,
  Map,
  MessageCircle,
  MessagesSquare,
  Package,
  PanelsTopLeft,
  Plug,
  ShieldCheck,
  UserCheck,
} from '@/components/ui/icons';
import type { LemmaIcon } from '@/components/ui/icons';

import { CONCEPTS, type ConceptEntry, type ConceptId } from '@/lib/education/concepts';
import type { DocsBlock, DocsPage } from '@/lib/data/docs';

const CONCEPT_ICONS: Record<ConceptId, LemmaIcon> = {
  pod: FolderOpen,
  agent: Bot,
  flow: GitBranch,
  table: Database,
  file: FileText,
  surface: MessageCircle,
  app: PanelsTopLeft,
  kit: Package,
  function: Code2,
  schedule: CalendarClock,
  connector: Plug,
  conversation: MessagesSquare,
  grant: ShieldCheck,
  scope: LockKeyhole,
  approval: UserCheck,
  runtime: Cpu,
  variable: Braces,
};

type ConceptDocsExtra = {
  useWhen: string[];
  getStarted: { title: string; items: string[] };
  whereItLives: string;
};

const CONCEPT_DOCS_EXTRA: Record<ConceptId, ConceptDocsExtra> = {
  pod: {
    useWhen: [
      'One team or process needs its own data, workers, and rules.',
      'Different processes should not see each other’s data.',
      'You want to share a whole working system with someone, not individual pieces.',
      'A kit or template needs a clean space to install into.',
    ],
    getStarted: {
      title: 'Create a pod that stays useful',
      items: [
        'Name it after the process, not the team — "Support triage", not "Acme Inc".',
        'Pick one primary unit of work: a ticket, a lead, a claim, an applicant.',
        'Add the first table for that unit of work before adding agents.',
        'Invite only the people who run this process; share resources wider later if needed.',
      ],
    },
    whereItLives: 'The pod switcher sits at the top of the left sidebar. Everything else in this guide lives inside a pod.',
  },
  agent: {
    useWhen: [
      'The work needs judgment: drafting, classifying, extracting, summarizing, deciding.',
      'Inbound items vary too much for fixed rules.',
      'A person currently does the step by reading and thinking, not by following a checklist.',
      'You want chat-style help that knows this pod’s data.',
    ],
    getStarted: {
      title: 'Hire your first agent',
      items: [
        'Open Agents in the sidebar and create one with a single, narrow job.',
        'Write instructions like a job description: what comes in, what should go out, what to do when unsure.',
        'Grant it an access scope — only the tables, folders, and apps the job needs.',
        'Run it once from chat with a real example before wiring it anywhere.',
      ],
    },
    whereItLives: 'Agents in the left sidebar. Each agent has Runs, Edit, and History tabs.',
  },
  flow: {
    useWhen: [
      'The work has more than one step and the steps have an order.',
      'Steps mix AI judgment with deterministic rules or human sign-off.',
      'You need to see where each item is and why it stopped.',
      'The same process should run the same way every time it starts.',
    ],
    getStarted: {
      title: 'Build a workflow that earns its keep',
      items: [
        'Write the process as a sentence first: "When X arrives, do A, then B, and ask a person before C."',
        'Create the tables, agents, and functions the steps will use.',
        'Open Workflows, add steps in order, and pick a work type for each.',
        'Put an approval before anything with real consequences.',
        'Run it once by hand and read the run step by step before scheduling it.',
      ],
    },
    whereItLives: 'Workflows in the left sidebar. The editor has a Steps view (list) and a Flow view (canvas).',
  },
  table: {
    useWhen: [
      'The data has fields you will filter, sort, or count on.',
      'Agents, flows, and people all need to read and update the same records.',
      'Work items move through states: open → in progress → done.',
      'You are tempted to track it in a spreadsheet outside Lemma.',
    ],
    getStarted: {
      title: 'Model your first table',
      items: [
        'Open Data and create a table named after the unit of work, plural: tickets, leads, claims.',
        'Add typed columns for what you will actually filter on — fewer is better to start.',
        'Add a status column with a default value so workflow steps have a state to move.',
        'Grant your agent access to the table and watch it write its first row.',
      ],
    },
    whereItLives: 'Data in the left sidebar. Tables and their records share the same view.',
  },
  file: {
    useWhen: [
      'Knowledge lives in documents: policies, contracts, manuals, transcripts.',
      'Agents should quote the real source instead of improvising.',
      'People attach evidence to work items.',
      'Content has no fixed fields — it is prose, not records.',
    ],
    getStarted: {
      title: 'Give the pod something to read',
      items: [
        'Open Docs and upload the documents your team actually consults.',
        'Organize into folders by topic — folders are the unit you grant to agents.',
        'Grant the relevant folder to the agent that needs it.',
        'Ask the agent a question the document answers and check that it cites it.',
      ],
    },
    whereItLives: 'Docs in the left sidebar. Personal files live under your own space; pod files are shared.',
  },
  surface: {
    useWhen: [
      'Work arrives in Slack, email, WhatsApp, or Teams and someone copy-pastes it in.',
      'Customers or teammates should reach the pod without learning a new tool.',
      'Approvals should be one click in the channel people already watch.',
      'You want the pod to reply where the conversation started.',
    ],
    getStarted: {
      title: 'Open the front door',
      items: [
        'Open Surfaces and pick the channel where work already arrives.',
        'Connect the account and choose which agent handles inbound messages.',
        'Set the routing rules — which channels, which mentions, which inbox labels.',
        'Send a test message from the channel and watch it appear in Conversations.',
      ],
    },
    whereItLives: 'Surfaces in the left sidebar. Each card shows a channel, its status, and its routing.',
  },
  app: {
    useWhen: [
      'Your team and the pod’s agents need to work in the same place, not just data to look at.',
      'People should drive the work while agents draft, answer, and run in the background.',
      'A process repeats daily and deserves a purpose-built, AI-powered screen.',
      'Someone outside the pod needs a safe, focused way to work with the pod’s agents.',
    ],
    getStarted: {
      title: 'Ship an agentic app',
      items: [
        'Open Apps and create one — describe what the team should be able to do and let AI draft it, or build by hand.',
        'Put the work front and center: the records, drafts, or decisions people act on.',
        'Embed agents where the work happens — drafting, summarizing, suggesting — so the app does work, not just display it.',
        'Share the URL; access follows the pod’s sharing rules, and teammates never see the configuration.',
      ],
    },
    whereItLives: 'Apps in the left sidebar. Each app deploys at its own URL.',
  },
  kit: {
    useWhen: [
      'You are starting a common process and don’t want to design from zero.',
      'You want to see how a working pod is wired before building your own.',
      'A new pod needs structure fast — apps, agents, tables, schedules — and you will customize from there.',
      'Someone built a setup you want to reuse and they shared it as a kit.',
    ],
    getStarted: {
      title: 'Install and make it yours',
      items: [
        'Open Kits and read what each kit installs — apps, agents, tables, schedules, and workflows are listed.',
        'Install into your pod; everything the kit bundles appears at once, already wired together.',
        'Open each piece it created and rename, rewrite, or delete freely — installed resources are fully yours.',
        'Run the kit’s app or example workflow once to see the intended shape of the process.',
      ],
    },
    whereItLives: 'Kits in the left sidebar, and featured kits appear on the pod home.',
  },
  function: {
    useWhen: [
      'The step has exact rules: scoring, validation, math, formatting.',
      'The same input must always produce the same output.',
      'An API call needs retries and typed inputs, not improvisation.',
      'An agent keeps being asked to do arithmetic or apply a rulebook.',
    ],
    getStarted: {
      title: 'Write the deterministic step',
      items: [
        'Open Functions (via Workflows) and create one with a typed input schema.',
        'Keep it single-purpose: score-lead, validate-expense, format-reply.',
        'Run it standalone with realistic input and check the output shape.',
        'Use it as a workflow step or call it from an agent that needs the rulebook applied.',
      ],
    },
    whereItLives: 'Functions, reachable from the Workflows section.',
  },
  schedule: {
    useWhen: [
      'You ask for the same report, sync, or sweep on a rhythm.',
      'Work should start at a time, not from a message.',
      'A flow needs to run nightly, hourly, or every Monday morning.',
    ],
    getStarted: {
      title: 'Put the pod on a rhythm',
      items: [
        'Open Schedules and create one: pick what runs (an agent or workflow).',
        'Choose when: a cadence, or a custom cron expression.',
        'Optionally add a condition so it stands down when there is nothing to do.',
        'Check the pod home — the next run appears under Upcoming.',
      ],
    },
    whereItLives: 'Schedules in the left sidebar; upcoming runs also show on the pod home.',
  },
  connector: {
    useWhen: [
      'Agents or flows should act in Salesforce, GitHub, Notion, Gmail — not just talk about them.',
      'The pod needs to read state that lives in another product.',
      'A workflow step ends with "…and then update it in the other system."',
    ],
    getStarted: {
      title: 'Give the pod hands',
      items: [
        'Open Connectors and connect the app with the account that should act.',
        'Decide the account mode: each user acts as themselves, or one fixed service account.',
        'Grant the connector to the specific agents and functions that need it.',
        'Test one real operation from a conversation before trusting it in a flow.',
      ],
    },
    whereItLives: 'Connectors in the left sidebar; connected accounts are workspace-wide.',
  },
  conversation: {
    useWhen: [
      'You want to ask the pod for something in plain language.',
      'You need to audit what an agent actually did and why.',
      'Work arrived from a surface and you want the full thread.',
    ],
    getStarted: {
      title: 'Read a conversation like an audit log',
      items: [
        'Open any conversation from the sidebar history or the Conversations list.',
        'Expand the tool calls — every table write, file search, and app action is recorded.',
        'Use this view when an agent surprises you: the reasoning trail is the debugging tool.',
      ],
    },
    whereItLives: 'The chat on pod home starts conversations; history lives in the left sidebar.',
  },
  grant: {
    useWhen: [
      'A resource should stay private while you draft it.',
      'A table holds data only some people should see.',
      'An app or agent is ready for the whole organization.',
    ],
    getStarted: {
      title: 'Share deliberately',
      items: [
        'Open any resource and use the Share button.',
        'Pick the level: private (you), pod (members of this pod), or organization.',
        'Remember this controls people. What an agent can touch is its access scope — set separately.',
      ],
    },
    whereItLives: 'The Share button on every resource — agents, tables, apps, files.',
  },
  scope: {
    useWhen: [
      'You are creating an agent and deciding what it may read and write.',
      'An agent should work the tickets table but never see payroll.',
      'A function needs exactly one external app operation, no more.',
    ],
    getStarted: {
      title: 'Scope like you mean it',
      items: [
        'In the agent editor, open the Access section and click Manage.',
        'Grant specific tables, folders, tools, and apps — start from nothing and add.',
        'Re-check the scope when the agent’s job changes; scopes should follow the job.',
      ],
    },
    whereItLives: 'The Access section in each agent’s editor.',
  },
  approval: {
    useWhen: [
      'A flow step sends money, emails a customer, or deletes records.',
      'Compliance wants a named person on the hook for a decision.',
      'You want automation to draft and humans to decide.',
    ],
    getStarted: {
      title: 'Add the human gate',
      items: [
        'In the workflow editor, add a step where the consequential action happens.',
        'Route the approval to a specific person or role.',
        'Approvals arrive in the app and on connected surfaces like Slack — deciding is one click.',
      ],
    },
    whereItLives: 'Approval steps inside the workflow editor; pending ones reach the assignee directly.',
  },
  variable: {
    useWhen: [
      'Something downstream — a workflow, an app, another agent — needs to read the agent’s output by field.',
      'You want the agent to always take and return the same shape.',
      'A workflow needs to branch on a value the agent produces.',
      'You are wiring an agent into a flow rather than just chatting with it.',
    ],
    getStarted: {
      title: 'Give the agent a contract',
      items: [
        'In the agent editor, open the Variables section and click Edit.',
        'Declare the inputs it needs — name and type each one.',
        'Declare the output fields downstream steps will read.',
        'Run it once and confirm the output matches the shape you defined.',
      ],
    },
    whereItLives: 'The Variables section in each agent’s editor.',
  },
  runtime: {
    useWhen: [
      'A judgment-heavy agent needs the strongest model available.',
      'A high-volume triage agent should be fast and cheap.',
      'You want one pod to mix both without compromise.',
    ],
    getStarted: {
      title: 'Match the brain to the job',
      items: [
        'In the agent editor, open the Model section.',
        'Leave the pod default for most agents; pin a runtime when the job clearly wants speed or depth.',
        'Revisit after watching real runs — the run history shows where quality or latency hurts.',
      ],
    },
    whereItLives: 'The Model section in each agent’s editor.',
  },
};

function buildConceptPage(entry: ConceptEntry): DocsPage {
  const extra = CONCEPT_DOCS_EXTRA[entry.id];
  const related = entry.related.map((id) => CONCEPTS[id]);

  const blocks: DocsBlock[] = [
    {
      type: 'paragraph',
      title: `What a ${entry.term.toLowerCase()} is`,
      body: entry.explainer.join(' '),
    },
    {
      type: 'callout',
      tone: 'note',
      title: 'For example',
      body: entry.example,
    },
    {
      type: 'list',
      title: 'Reach for it when',
      items: extra.useWhen,
    },
    {
      type: 'steps',
      title: extra.getStarted.title,
      items: extra.getStarted.items,
    },
    {
      type: 'paragraph',
      title: 'Where it lives',
      body: extra.whereItLives,
    },
  ];

  if (related.length > 0) {
    blocks.push({
      type: 'table',
      title: 'Related concepts',
      columns: ['Concept', 'In short'],
      rows: related.map((rel) => [rel.term, rel.oneLiner]),
    });
  }

  return {
    slug: entry.guideSlug,
    title: entry.term,
    eyebrow: 'Concept',
    group: 'Concepts',
    icon: CONCEPT_ICONS[entry.id],
    description: entry.oneLiner,
    blocks,
  };
}

const CONCEPT_PAGE_ORDER: ConceptId[] = [
  'pod',
  'agent',
  'flow',
  'table',
  'file',
  'surface',
  'app',
  'kit',
  'function',
  'schedule',
  'connector',
  'conversation',
  'grant',
  'scope',
  'approval',
  'runtime',
  'variable',
];

export const conceptDocsPages: DocsPage[] = CONCEPT_PAGE_ORDER.map((id) =>
  buildConceptPage(CONCEPTS[id])
);

/**
 * Stubs for the two custom-rendered pages. Their static routes
 * (app/docs/how-lemma-works, app/docs/glossary) take precedence over the
 * [...slug] catch-all; these entries exist so nav and search know about them.
 */
export const howLemmaWorksStub: DocsPage = {
  slug: 'how-lemma-works',
  title: 'How Lemma works',
  eyebrow: 'Concept',
  group: 'Concepts',
  icon: Map,
  description:
    'The whole system on one screen: work arrives through surfaces, agents and workflows move it over tables and files, people steer from apps and approvals.',
  blocks: [
    {
      type: 'paragraph',
      title: 'The mental model',
      body:
        'Surfaces bring work in. Inside the pod, agents handle judgment, functions handle rules, and workflows chain them with human approvals. Tables and files hold the state everything reads and writes. Apps and conversations are where people watch and steer. Schedules make it run on a rhythm; connectors let it act in your other tools.',
    },
  ],
};

export const glossaryStub: DocsPage = {
  slug: 'glossary',
  title: 'Glossary',
  eyebrow: 'Concept',
  group: 'Concepts',
  icon: FileText,
  description: 'Every Lemma term, defined in one line, A to Z.',
  blocks: CONCEPT_PAGE_ORDER.map((id) => ({
    type: 'paragraph' as const,
    title: CONCEPTS[id].term,
    body: CONCEPTS[id].oneLiner,
  })),
};

export const conceptDocsGroup = {
  title: 'Concepts',
  pages: [
    'how-lemma-works',
    ...CONCEPT_PAGE_ORDER.map((id) => CONCEPTS[id].guideSlug),
    'glossary',
  ],
};
