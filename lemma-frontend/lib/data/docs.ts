import {
  Bot,
  Boxes,
  Code2,
  Database,
  GitBranch,
  KeyRound,
  LayoutDashboard,
  LockKeyhole,
  Package,
  PlayCircle,
  Terminal,
  Workflow,
} from 'lucide-react';
import type { LucideIcon } from 'lucide-react';

import {
  conceptDocsGroup,
  conceptDocsPages,
  glossaryStub,
  howLemmaWorksStub,
} from '@/lib/data/concept-docs';
import { educationGuidePages } from '@/lib/data/education-guides';

export type DocsBlock =
  | {
      type: 'paragraph';
      title?: string;
      body: string;
    }
  | {
      type: 'list';
      title: string;
      body?: string;
      items: string[];
    }
  | {
      type: 'steps';
      title: string;
      body?: string;
      items: string[];
    }
  | {
      type: 'code';
      title: string;
      body?: string;
      language: string;
      code: string;
    }
  | {
      type: 'table';
      title: string;
      body?: string;
      columns: string[];
      rows: string[][];
    }
  | {
      type: 'callout';
      tone?: 'note' | 'warning' | 'success';
      title: string;
      body: string;
    };

export type DocsPage = {
  slug: string;
  title: string;
  eyebrow: string;
  description: string;
  group: string;
  icon: LucideIcon;
  blocks: DocsBlock[];
};

export type DocsGroup = {
  title: string;
  pages: string[];
};

const baseDocsPages: DocsPage[] = [
  {
    slug: 'getting-started',
    title: 'Quickstart',
    eyebrow: 'Start here',
    group: 'Start',
    icon: PlayCircle,
    description:
      'Create an account, run Lemma locally or in the cloud, install the CLI, and create your first pod.',
    blocks: [
      {
        type: 'paragraph',
        title: 'Choose your path',
        body:
          'Use Lemma Cloud when you want a hosted workspace, Lemma Stack when you want the full product on your machine, or the source checkout when you are contributing to the platform.',
      },
      {
        type: 'table',
        title: 'Which setup should you use?',
        columns: ['Path', 'Use it when', 'You get'],
        rows: [
          ['Lemma Cloud', 'You want to start without hosting anything.', 'An account at lemma.work and hosted pods.'],
          ['Local stack', 'You want the product running on your own machine.', 'Frontend, API, database, auth, Redis, and document processing in containers.'],
          ['Source checkout', 'You want to change Lemma itself.', 'Hot-reload backend, frontend, and agentbox from this repo.'],
        ],
      },
      {
        type: 'code',
        title: 'Cloud quickstart',
        body:
          'Create an account at lemma.work, then install the CLI and log in. Browser login will finish account setup if needed.',
        language: 'bash',
        code: `uv tool install lemma-terminal
lemma servers cloud --use
lemma auth login`,
      },
      {
        type: 'code',
        title: 'Local stack quickstart',
        body:
          'Run the released stack on your machine, open the app at http://localhost:3711, create an account, then point the CLI at the local server.',
        language: 'bash',
        code: `curl -fsSL https://raw.githubusercontent.com/lemma-work/lemma-platform/v0.5.4/install.sh | bash
uv tool install lemma-terminal
lemma servers select local
lemma auth login`,
      },
      {
        type: 'code',
        title: 'Developer checkout',
        body:
          'Use this path only when you are changing the platform source. The dev stack uses 3710/8710 so it can coexist with the installed local stack.',
        language: 'bash',
        code: `git clone https://github.com/lemma-work/lemma-platform.git
cd lemma-platform
make dev

uv tool install --force --editable lemma-cli
lemma servers add local-dev --base-url http://localhost:8710 --auth-url http://localhost:3710/auth
lemma servers select local-dev
lemma auth login`,
      },
      {
        type: 'code',
        title: 'Create a starter pod',
        body:
          'A pod is the workspace boundary: tables, files, agents, workflows, permissions, apps, and surfaces for one team or process.',
        language: 'bash',
        code: `lemma pod create my-team --with-starter
lemma pods select my-team --save-default
lemma pod describe`,
      },
      {
        type: 'code',
        title: 'Install SDKs for app and function code',
        body:
          'Use the TypeScript SDK in frontend apps and the Python SDK in pod function code.',
        language: 'bash',
        code: `# TypeScript SDK for app frontends
npm install lemma-sdk

# Python SDK for pod function code
uv pip install lemma-sdk`,
      },
      {
        type: 'code',
        title: 'Use the TypeScript SDK',
        body:
          'Point the client at a selected pod and start reading tables, running workflows, and chatting with agents.',
        language: 'ts',
        code: `import { LemmaClient } from "lemma-sdk";

const client = new LemmaClient({ podId: "<pod-id>" });
await client.initialize();

const tables = await client.tables.list();
const runs = await client.workflows.list();`,
      },
      {
        type: 'code',
        title: 'Write pod function code (Python)',
        body:
          'Inside a function, the SDK reads credentials from the pod environment automatically.',
        language: 'python',
        code: `from lemma_sdk import Pod

pod = Pod.from_env()
rows = pod.records.list("tickets", limit=10).to_dict()["items"]`,
      },
      {
        type: 'callout',
        tone: 'success',
        title: 'You are ready',
        body:
          'You now have an account, a server selected, the CLI installed, a starter pod, and SDK access from TypeScript and Python.',
      },
      {
        type: 'list',
        title: 'Next steps',
        items: [
          'Read the Overview to understand pods, resources, and the mental model.',
          'Follow First Pod to model tables, functions, agents, and workflows.',
          'Browse the CLI section for the full command reference.',
          'Check the SDK section for React hooks, auth, conversations, and data access.',
        ],
      },
    ],
  },
  {
    slug: 'overview',
    title: 'Overview',
    eyebrow: 'Start here',
    group: 'Start',
    icon: Boxes,
    description:
      'Lemma is an operating-system platform for business teams: pods combine data, agents, workflows, functions, connectors, files, and human surfaces around real work.',
    blocks: [
      {
        type: 'paragraph',
        title: 'What you are building',
        body:
          'A Lemma pod is a bounded operating system for one business use case. It owns the state, logic, automation, permissions, and operator surfaces for that use case. The goal is not to create a loose collection of AI helpers; the goal is to give a team a system that can run a repeatable operating loop.',
      },
      {
        type: 'list',
        title: 'Core resources',
        items: [
          'Tables and records store durable structured business state.',
          'Files store unstructured context such as briefs, contracts, manuals, transcripts, and attachments.',
          'Functions perform deterministic typed backend actions.',
          'Agents handle judgment-heavy work such as drafting, classification, extraction, research, and summarization.',
          'Workflows orchestrate multi-step processes, waits, branching, schedules, and app-triggered runs.',
          'Apps provide full operator interfaces for repeatable human workflows.',
          'Conversations provide user-facing or agent-scoped interaction with instructions, metadata, tools, and streaming output.',
        ],
      },
      {
        type: 'callout',
        title: 'Mental model',
        body:
          'Models provide intelligence. Lemma provides rails: state, permissions, connectors, deterministic tools, human review, auditability, and interfaces.',
      },
      {
        type: 'table',
        title: 'When to use each layer',
        columns: ['Need', 'Use'],
        rows: [
          ['Single-table create/update', 'Record API'],
          ['Validation, coordinated writes, external calls', 'Function'],
          ['Drafting, research, classification, extraction', 'Agent through task or conversation'],
          ['Stages, branching, waits, schedules, triggers', 'Workflow'],
          ['Primary user interaction is chat', 'Conversation / assistant surface'],
          ['People need queues, forms, detail views, and actions', 'App'],
          ['Documents, attachments, source material', 'Files'],
        ],
      },
    ],
  },
  {
    slug: 'quickstart',
    title: 'First Pod',
    eyebrow: 'Build loop',
    group: 'Start',
    icon: PlayCircle,
    description:
      'The recommended order for turning a blank workspace into a useful pod.',
    blocks: [
      {
        type: 'steps',
        title: 'Recommended sequence',
        items: [
          'Define the operating job: one team or process, one domain, one primary unit of work.',
          'Create the pod and inspect what the starter gave you.',
          'Create or refine collaborative tables for shared business records.',
          'Add one function only if a write needs validation, coordinated records, or an external operation.',
          'Add an agent for one judgment-heavy step, with explicit input and output schemas.',
          'Create a workflow only when there is real orchestration: branching, waits, schedules, or handoffs.',
          'Build the app last, against verified resources, while keeping the first screen focused on real work.',
        ],
      },
      {
        type: 'code',
        title: 'CLI rhythm',
        language: 'bash',
        code: `lemma pod create support-triage --with-starter --description "Triage and route inbound support work"
lemma pods select support-triage --save-default
lemma pod describe

lemma table create --pod-id <pod-id> --payload-file ./payloads/tickets-table.json
lemma function create --pod-id <pod-id> --payload-file ./payloads/escalate-ticket-function.json
lemma agent create --pod-id <pod-id> --payload-file ./payloads/ticket-triage-agent.json
lemma workflow create --pod-id <pod-id> --payload-file ./payloads/ticket-triage-workflow.json
lemma pod describe <pod-id>`,
      },
      {
        type: 'callout',
        tone: 'warning',
        title: 'Do not skip verification',
        body:
          'Before wiring an app or workflow node, run the underlying function, inspect agent output, and verify table records with realistic sample data.',
      },
    ],
  },
  {
    slug: 'platform/pods-and-scope',
    title: 'Pods and Scope',
    eyebrow: 'Platform',
    group: 'Platform',
    icon: Boxes,
    description:
      'How to scope a pod so it stays useful, bounded, and easy to evolve.',
    blocks: [
      {
        type: 'paragraph',
        title: 'Pod',
        body:
          'A pod is the technical and operational boundary for one team or process. It contains the tables, files, agents, workflows, functions, permissions, apps, and surfaces that make that work one system.',
      },
      {
        type: 'paragraph',
        title: 'Operating job',
        body:
          'Scope the pod around the work it moves: triage support, qualify leads, review expenses, onboard teammates, track launch items, or run a back-office loop. Multiple apps, workflows, agents, and assistants can live inside one pod when they serve the same operating job.',
      },
      {
        type: 'list',
        title: 'Good pod scope',
        items: [
          'One team or one operating domain.',
          'One primary unit of work, such as ticket, lead, claim, expense, applicant, or launch item.',
          'One shared data model for the domain.',
          'Multiple user surfaces only when they serve materially different personas in the same operating loop.',
        ],
      },
      {
        type: 'list',
        title: 'Bad pod scope',
        items: [
          'Mixing unrelated domains such as hiring, support, finance, and sales in one pod.',
          'Creating mirror membership tables instead of using organization and pod membership APIs.',
          'Starting with an app layout before deciding what work object the operator acts on.',
        ],
      },
    ],
  },
  {
    slug: 'platform/resources',
    title: 'Resources',
    eyebrow: 'Platform',
    group: 'Platform',
    icon: Database,
    description:
      'The resource map for tables, files, functions, agents, workflows, conversations, connectors, and apps.',
    blocks: [
      {
        type: 'table',
        title: 'Resource responsibilities',
        columns: ['Resource', 'Owns', 'Avoid using it for'],
        rows: [
          ['Table', 'Durable structured state', 'Unstructured documents or binary assets'],
          ['Record API', 'Simple table CRUD', 'Multi-table writes with business rules'],
          ['File', 'Documents and attachments', 'State that needs filtering, sorting, or workflow transitions'],
          ['Function', 'Typed deterministic logic', 'Single-row inserts with no business logic'],
          ['Agent', 'Judgment-heavy reasoning', 'Deterministic writes or hidden side effects'],
          ['Workflow', 'Orchestration over time', 'One-shot actions with no real process'],
          ['Conversation', 'Interactive or agent-scoped message flow', 'Durable business data'],
          ['App', 'Repeatable operator workflow', 'Static marketing or dashboard posters'],
          ['Connector', 'External system access', 'Pod-local business state'],
        ],
      },
      {
        type: 'callout',
        title: 'Build order',
        body:
          'For real pods, provision connectors, functions, workflows, and apps in that order. Apps are imagined early, but wired last against verified upstream resources.',
      },
    ],
  },
  {
    slug: 'platform/data-modeling',
    title: 'Data Modeling',
    eyebrow: 'Platform',
    group: 'Platform',
    icon: Database,
    description:
      'How to choose tables versus files, collaborative versus personal state, and record APIs versus SQL.',
    blocks: [
      {
        type: 'list',
        title: 'Table rules',
        items: [
          'Use tables for typed fields, filtering, sorting, structured updates, and workflow state.',
          'Use collaborative tables for shared work such as tickets, leads, approvals, claims, and expenses.',
          'Use personal RLS tables only when each caller should see only their own rows.',
          'Never send created_at, updated_at, or system-managed user_id in record payloads.',
          'Use owner_user_id, assignee_user_id, creator_user_id, or reporter_user_id for explicit business ownership.',
        ],
      },
      {
        type: 'list',
        title: 'File rules',
        items: [
          'Use files for contracts, manuals, reports, workflows, screenshots, transcripts, and attachments.',
          'Uploading a file does not inject its text into an agent or workflow automatically.',
          'Store a stable path or identifier, grant the relevant folder, and make the runtime fetch or search it explicitly.',
          'Search indexing is asynchronous, so verify search behavior after upload before relying on it.',
        ],
      },
      {
        type: 'code',
        title: 'Collaborative table',
        language: 'bash',
        code: `lemma table create --pod-id <pod-id> --payload '{
  "name": "tickets",
  "enable_rls": false,
  "columns": [
    {"name": "title", "type": "TEXT", "required": true},
    {"name": "status", "type": "TEXT", "required": true, "default": "OPEN"},
    {"name": "assignee_user_id", "type": "UUID"}
  ]
}'`,
      },
    ],
  },
  {
    slug: 'sdk/installation',
    title: 'Installation',
    eyebrow: 'SDK',
    group: 'SDK',
    icon: Package,
    description:
      'Install lemma-sdk, configure the client, and choose the headless or registry path.',
    blocks: [
      {
        type: 'callout',
        title: 'Install the CLI too',
        body:
          'If you have not already, install the Lemma CLI (lemma-terminal) to create pods, manage resources, and scaffold apps. See the Getting Started guide for the full setup.',
      },
      {
        type: 'code',
        title: 'Install the SDK',
        language: 'bash',
        code: `npm install lemma-sdk`,
      },
      {
        type: 'code',
        title: 'Create a client',
        body:
          'The SDK resolves API/auth URLs from explicit overrides, window config, or environment variables. Defaults point at the hosted Lemma services.',
        language: 'ts',
        code: `import { LemmaClient } from "lemma-sdk";

const client = new LemmaClient({
  apiUrl: import.meta.env.VITE_LEMMA_API_URL,
  authUrl: import.meta.env.VITE_LEMMA_AUTH_URL,
  podId: import.meta.env.VITE_LEMMA_POD_ID,
});

await client.initialize();`,
      },
      {
        type: 'table',
        title: 'Environment names',
        columns: ['Runtime', 'Supported names'],
        rows: [
          ['Vite', 'VITE_LEMMA_API_URL, VITE_LEMMA_AUTH_URL, VITE_LEMMA_POD_ID'],
          ['CRA / webpack', 'REACT_APP_LEMMA_API_URL, REACT_APP_LEMMA_AUTH_URL, REACT_APP_LEMMA_POD_ID'],
          ['Node', 'LEMMA_API_URL, LEMMA_AUTH_URL, LEMMA_POD_ID'],
          ['Browser', 'window.__LEMMA_CONFIG__'],
        ],
      },
      {
        type: 'code',
        title: 'Optional registry setup',
        language: 'bash',
        code: `npx lemma-sdk init-shadcn
npx shadcn@latest add @lemma/lemma-records-view
npx shadcn@latest add @lemma/lemma-assistant-experience`,
      },
    ],
  },
  {
    slug: 'sdk/client',
    title: 'Core Client',
    eyebrow: 'SDK',
    group: 'SDK',
    icon: Code2,
    description:
      'The namespace map exposed by LemmaClient and how to work with pod-scoped versus org/user surfaces.',
    blocks: [
      {
        type: 'table',
        title: 'Namespaces',
        columns: ['Scope', 'Namespaces'],
        rows: [
          ['Pod-scoped', 'tables, records, files, functions, agents, conversations, workflows, apps, resources, schedules, datastore'],
          ['Org and user', 'users, organizations, pods, podMembers, podJoinRequests, podSurfaces, icons'],
          ['External systems', 'connectors'],
        ],
      },
      {
        type: 'code',
        title: 'Switch pod scope',
        language: 'ts',
        code: `const rootClient = new LemmaClient({ apiUrl, authUrl });
const podClient = rootClient.withPod("<pod-id>");

const tables = await podClient.tables.list();
const currentUser = await podClient.users.current();`,
      },
      {
        type: 'callout',
        title: 'Escape hatch',
        body:
          'Use client.request<T>(method, path, options) only when a generated API exists but no typed namespace wrapper has landed yet.',
      },
    ],
  },
  {
    slug: 'sdk/react-auth',
    title: 'React Auth and Pod Access',
    eyebrow: 'SDK',
    group: 'SDK',
    icon: LockKeyhole,
    description:
      'Use AuthGuard and pod-access hooks to give apps a real request-access flow instead of a dead end.',
    blocks: [
      {
        type: 'code',
        title: 'Root guard',
        language: 'tsx',
        code: `import { AuthGuard } from "lemma-sdk/react";

export function App({ client }: { client: LemmaClient }) {
  return (
    <AuthGuard client={client} appName="Support Triage">
      <SupportTriageApp client={client} />
    </AuthGuard>
  );
}`,
      },
      {
        type: 'list',
        title: 'What AuthGuard handles',
        items: [
          'Checks signed-in state through Lemma auth.',
          'Uses pod membership when the client has a pod id.',
          'Shows a branded sign-in fallback when unauthenticated.',
          'Shows request-access UI when signed in but not a pod member.',
          'Lets you pass custom loading, unauthenticated, or access-request fallbacks.',
        ],
      },
      {
        type: 'code',
        title: 'Headless pod access',
        language: 'tsx',
        code: `const access = usePodAccess({ client });

if (access.status === "missing") {
  return <button onClick={() => void access.requestAccess()}>Request access</button>;
}

return <span>{access.member?.roles?.join(", ") ?? "No pod role"}</span>;`,
      },
    ],
  },
  {
    slug: 'sdk/conversations',
    title: 'Conversations',
    eyebrow: 'SDK',
    group: 'SDK',
    icon: Bot,
    description:
      'Conversation-first agent and assistant interactions, including instructions, metadata, streaming, and final output.',
    blocks: [
      {
        type: 'callout',
        title: 'Correct pattern',
        body:
          'Create a conversation first, then send messages with the returned conversation id. Do not use create-if-missing message helpers for new code.',
      },
      {
        type: 'code',
        title: 'Create, then send',
        language: 'ts',
        code: `const thread = await client.conversations.createForAgent("triage_agent", {
  title: "Triage ticket ticket_123",
  instructions: "Triage the ticket, propose the next owner, and explain confidence.",
  metadata: { source: "support_queue", ticket_id: "ticket_123" },
  type: "TASK",
});

await client.conversations.messages.send(thread.id, {
  content: JSON.stringify({
    ticket_id: "ticket_123",
    prompt: "Triage this ticket.",
  }),
  metadata: { source: "support_queue", ticket_id: "ticket_123" },
});`,
      },
      {
        type: 'code',
        title: 'React hook',
        language: 'tsx',
        code: `const conversation = useConversationMessages({
  client,
  agentName: "triage_agent",
  autoResume: true,
});

await conversation.createConversation({
  title: "Triage ticket ticket_123",
  instructions: "Return owner, priority, and reasoning.",
  metadata: { ticket_id: "ticket_123" },
  type: "TASK",
  setActive: true,
});

await conversation.sendMessage("Please triage ticket_123.", {
  conversationId: conversation.conversationId,
  metadata: { ticket_id: "ticket_123" },
});`,
      },
      {
        type: 'table',
        title: 'Use the right surface',
        columns: ['Need', 'Surface'],
        rows: [
          ['List previous conversations', 'useConversations'],
          ['Load one conversation', 'useConversation'],
          ['Stream messages and final output', 'useConversationMessages'],
          ['Build full assistant UI behavior', 'useAssistantController / useAssistantRuntime'],
          ['Inspect agent input schema', 'useAgentInputSchema'],
        ],
      },
    ],
  },
  {
    slug: 'sdk/data-and-files',
    title: 'Data and Files',
    eyebrow: 'SDK',
    group: 'SDK',
    icon: Database,
    description:
      'Build custom records, forms, relational views, search, and document workspaces from headless hooks.',
    blocks: [
      {
        type: 'table',
        title: 'Data hooks',
        columns: ['Job', 'Hook'],
        rows: [
          ['List records', 'useRecords'],
          ['Fetch one record', 'useRecord'],
          ['Create/update/delete', 'useCreateRecord, useUpdateRecord, useDeleteRecord'],
          ['Schema-driven forms', 'useRecordForm, useRecordSchema, useForeignKeyOptions'],
          ['Joins and related records', 'useJoinedRecords, useRelatedRecords, useReverseRelatedRecords'],
          ['Custom SQL reads and aggregates', 'useDatastoreQuery, useRecordAggregates'],
        ],
      },
      {
        type: 'table',
        title: 'File hooks',
        columns: ['Job', 'Hook'],
        rows: [
          ['Browse folder', 'useFiles'],
          ['Upload', 'useUploadFile'],
          ['Rename or move', 'useUpdateFile'],
          ['Delete', 'useDeleteFile'],
          ['Create folder', 'useCreateFolder'],
          ['Search files', 'useFileSearch'],
          ['Directory tree', 'useFileTree'],
          ['Preview content', 'useFilePreview'],
          ['Search records and files together', 'useGlobalSearch'],
        ],
      },
      {
        type: 'code',
        title: 'Function-backed record form',
        language: 'tsx',
        code: `const form = useRecordForm({
  client,
  tableName: "issues",
  mode: "create",
  submitVia: "function",
  submitFunctionName: "create-issue",
  submitFunctionInput: (payload) => ({
    title: payload.title,
    priority: payload.priority,
  }),
});`,
      },
    ],
  },
  {
    slug: 'sdk/workflows-and-functions',
    title: 'Workflows and Functions',
    eyebrow: 'SDK',
    group: 'SDK',
    icon: Workflow,
    description:
      'Start workflows, poll runs, resume human waits, and call deterministic functions from app surfaces.',
    blocks: [
      {
        type: 'table',
        title: 'Workflow hooks',
        columns: ['Job', 'Hook'],
        rows: [
          ['Start a run with schema support', 'useWorkflowStart'],
          ['Start or poll one known workflow', 'useWorkflowRun'],
          ['List runs', 'useWorkflowRuns'],
          ['Show waits assigned to current pod member', 'useWorkflowRunWaitAssignments'],
          ['Resume a run', 'useWorkflowResume'],
          ['Legacy flow naming', 'useFlowSession, useFlowRunHistory'],
        ],
      },
      {
        type: 'code',
        title: 'Start a workflow',
        language: 'tsx',
        code: `const workflow = useWorkflowRun({
  client,
  workflowName: "approve_ticket",
});

await workflow.start({ ticket_id: "ticket_123" });`,
      },
      {
        type: 'code',
        title: 'Run a function',
        language: 'tsx',
        code: `const escalation = useFunctionRun({
  client,
  functionName: "escalate-ticket",
});

await escalation.run({
  ticket_id: "ticket_123",
  reason: "SLA breach risk",
});`,
      },
    ],
  },
  {
    slug: 'sdk/registry',
    title: 'Registry Blocks',
    eyebrow: 'SDK',
    group: 'SDK',
    icon: LayoutDashboard,
    description:
      'Install optional UI blocks when you want stock Lemma components on top of the headless SDK.',
    blocks: [
      {
        type: 'code',
        title: 'Configure registry',
        language: 'bash',
        code: `npx lemma-sdk init-shadcn`,
      },
      {
        type: 'table',
        title: 'Canonical blocks',
        columns: ['Area', 'Blocks'],
        rows: [
          ['Records', 'lemma-records-view, lemma-detail-panel, lemma-record-form, lemma-status-flow'],
          ['Search and files', 'lemma-global-search, lemma-file-browser, lemma-document-workspace, lemma-markdown-editor, lemma-page-tree'],
          ['Collaboration', 'lemma-comments, lemma-activity-feed, lemma-insights, lemma-action-surface'],
          ['Workflow and shell', 'lemma-workflow-runner, lemma-members, lemma-notification-bell, lemma-user-menu'],
          ['Agent experience', 'lemma-assistant-experience'],
        ],
      },
      {
        type: 'callout',
        title: 'Registry is not architecture',
        body:
          'Use registry blocks when they fit the operator job. For custom review, triage, approval, or queue workflows, build the route locally and use hooks first.',
      },
    ],
  },
  {
    slug: 'cli/overview',
    title: 'CLI Overview',
    eyebrow: 'CLI',
    group: 'CLI',
    icon: Terminal,
    description:
      'The Lemma CLI manages organizations, pods, resources, runs, conversations, connectors, and app bundles.',
    blocks: [
      {
        type: 'code',
        title: 'Global options',
        language: 'bash',
        code: `lemma --base-url https://api.lemma.work --auth-url https://lemma.work/auth --output json <command>`,
      },
      {
        type: 'table',
        title: 'Top-level command groups',
        columns: ['Group', 'Purpose'],
        rows: [
          ['auth, config, ls', 'Authentication, local config, org/pod tree snapshot'],
          ['organization, pod', 'Workspace and pod lifecycle'],
          ['table, record, query, file', 'Data and file management'],
          ['function, agent, task, assistant, conversation', 'AI and deterministic runtime resources'],
          ['workflow', 'Workflow definitions, installs, and runs'],
          ['app', 'App metadata, clone, and deploy'],
          ['connector', 'Connectors, operations, triggers, and connected accounts'],
          ['operation, web, tool', 'OpenAPI inspection, web tools, and agent tooling'],
        ],
      },
      {
        type: 'list',
        title: 'Payload habits',
        items: [
          'Use --payload-file for larger JSON to avoid shell escaping drift.',
          'Use --output json when saving artifacts or piping results.',
          'Use pod describe before changing a live pod so you know what exists.',
          'Use flat command names such as workflow graph-update and workflow run-start.',
        ],
      },
    ],
  },
  {
    slug: 'cli/auth-and-context',
    title: 'Auth and Context',
    eyebrow: 'CLI',
    group: 'CLI',
    icon: KeyRound,
    description:
      'Authenticate, inspect org/pod context, and pass environment-specific URLs safely.',
    blocks: [
      {
        type: 'code',
        title: 'Inspect context',
        language: 'bash',
        code: `lemma auth --help
lemma config --help
lemma ls`,
      },
      {
        type: 'table',
        title: 'Connection options',
        columns: ['Option', 'Use'],
        rows: [
          ['--base-url', 'Backend API base URL'],
          ['--auth-url', 'Auth frontend URL for browser login'],
          ['--token', 'Bearer token, also read from LEMMA_TOKEN or config'],
          ['--config-file', 'Alternate config path, default ~/.lemma/config.json'],
          ['--no-verify-ssl', 'Local/self-signed development only'],
          ['--output json', 'Machine-readable output for scripts and artifacts'],
        ],
      },
      {
        type: 'callout',
        title: 'Local backend note',
        body:
          'If https://api.localhost returns SSL or proxy failures in local backend work, use explicit 127.0.0.1 base URLs before assuming the CLI command is broken.',
      },
    ],
  },
  {
    slug: 'cli/pods-data-files',
    title: 'Pods, Data, and Files',
    eyebrow: 'CLI',
    group: 'CLI',
    icon: Database,
    description:
      'Create pods, inspect resources, manage tables and records, and work with pod files.',
    blocks: [
      {
        type: 'table',
        title: 'Pod commands',
        columns: ['Command', 'Use'],
        rows: [
          ['pod list/create/get/update/delete', 'Pod lifecycle'],
          ['pod config', 'Fetch runtime config'],
          ['pod describe', 'Snapshot metadata, agents, assistants, functions, tables, workflows, and file roots'],
          ['pod member-list/member-add/member-update-role/member-remove', 'Pod roster operations'],
          ['pod export/import', 'Export or apply pod resource folder trees'],
        ],
      },
      {
        type: 'code',
        title: 'Data snapshot',
        language: 'bash',
        code: `lemma table list --pod-id <pod-id>
lemma table describe tickets --pod-id <pod-id>
lemma record list tickets --pod-id <pod-id>
lemma query execute --pod-id <pod-id> "SELECT * FROM tickets LIMIT 20"
lemma file list / --pod-id <pod-id>
lemma file search onboarding --pod-id <pod-id> --scope-path /manuals --scope-mode SUBTREE`,
      },
      {
        type: 'callout',
        tone: 'warning',
        title: 'RLS and system fields',
        body:
          'Do not write user_id, created_at, or updated_at. For collaborative ownership, store explicit business fields and resolve people through pod member APIs.',
      },
    ],
  },
  {
    slug: 'cli/functions-agents',
    title: 'Functions and Agents',
    eyebrow: 'CLI',
    group: 'CLI',
    icon: Bot,
    description:
      'Create deterministic backend functions, create judgment-heavy agents, and verify both before orchestration.',
    blocks: [
      {
        type: 'code',
        title: 'Function lifecycle',
        language: 'bash',
        code: `lemma function create --pod-id <pod-id> --payload-file ./payloads/create-expense-function.json
lemma function list --pod-id <pod-id>
lemma function get create-expense --pod-id <pod-id>
lemma function run create-expense --pod-id <pod-id> --payload '{"input_data":{"merchant":"Uber","amount":19.75}}'
lemma function run-list create-expense --pod-id <pod-id>`,
      },
      {
        type: 'code',
        title: 'Agent lifecycle',
        language: 'bash',
        code: `lemma agent create --pod-id <pod-id> --payload-file ./payloads/document-summarizer-agent.json
lemma agent list --pod-id <pod-id>
lemma agent get document-summarizer --pod-id <pod-id>
lemma task create --pod-id <pod-id> --agent-name document-summarizer --payload-file ./payloads/task-create.json
lemma task get <task-id> --pod-id <pod-id>`,
      },
      {
        type: 'table',
        title: 'Use the right runtime',
        columns: ['Need', 'Use'],
        rows: [
          ['Typed validation and writes', 'Function'],
          ['External app operation with retries', 'Function plus accessible_connectors'],
          ['Research, summarization, extraction, classification', 'Agent'],
          ['Background agent execution', 'Task or workflow AGENT node'],
          ['User-facing chat', 'Conversation or assistant surface'],
        ],
      },
    ],
  },
  {
    slug: 'cli/workflows',
    title: 'Workflows',
    eyebrow: 'CLI',
    group: 'CLI',
    icon: GitBranch,
    description:
      'Create workflow definitions, upload graphs, install triggers, start runs, and resume waits.',
    blocks: [
      {
        type: 'steps',
        title: 'Workflow build order',
        items: [
          'Design the SOP.',
          'Create tables, functions, and agents first.',
          'Run each function standalone and save the response shape.',
          'Create the workflow shell.',
          'Upload the graph with real function_name and agent_name values.',
          'Install the workflow if start type is scheduled, event, or datastore event.',
          'Run a realistic test and inspect run output.',
        ],
      },
      {
        type: 'code',
        title: 'Definition and graph',
        language: 'bash',
        code: `lemma workflow create --pod-id <pod-id> --payload-file ./payloads/workflow-create.json
lemma workflow graph-update expense-review --pod-id <pod-id> --payload-file ./payloads/workflow-graph.json
lemma workflow get expense-review --pod-id <pod-id>`,
      },
      {
        type: 'code',
        title: 'Installs and runs',
        language: 'bash',
        code: `lemma workflow install-create expense-review --pod-id <pod-id> --schedule-type CRON --cron-expression '*/5 * * * *' --timezone UTC
lemma workflow run-start expense-review --pod-id <pod-id> --payload '{"input":{"expense_id":"exp_123"}}'
lemma workflow run-get expense-review <run-id> --pod-id <pod-id>
lemma workflow run-resume expense-review <run-id> --pod-id <pod-id> --payload-file ./payloads/resume.json`,
      },
      {
        type: 'callout',
        tone: 'warning',
        title: 'Manual workflow threshold',
        body:
          'A manual workflow should usually have at least two substantive stages after intake, or a clear need for branching, waiting, looping, approval handoffs, or resumability.',
      },
    ],
  },
  {
    slug: 'cli/apps',
    title: 'Apps',
    eyebrow: 'CLI',
    group: 'CLI',
    icon: LayoutDashboard,
    description:
      'Clone, build, and deploy operator workbenches while preserving the app shell and routing.',
    blocks: [
      {
        type: 'list',
        title: 'App rules',
        items: [
          'An app is a workbench, not a landing page.',
          'The first visible screen should start with work.',
          'One active work object should usually anchor the interface.',
          'Actions should sit close to the object they affect.',
          'Preserve scaffolded routing and shell unless the workflow truly needs a change.',
        ],
      },
      {
        type: 'code',
        title: 'CLI lifecycle',
        language: 'bash',
        code: `lemma app list --pod-id <pod-id>
lemma app get support-triage --pod-id <pod-id>
lemma app clone support-triage --pod-id <pod-id> ./support-triage-app
cd ./support-triage-app
npm install
npm run build
lemma app deploy support-triage --pod-id <pod-id> --source-dir .`,
      },
      {
        type: 'code',
        title: 'Scaffold a Vite app',
        language: 'bash',
        code: `lemma apps init support-triage \
  --pod <pod-id> \
  --title "Support Triage" \
  --nav sidebar \
  --style soft \
  --template /path/to/lemma-template-vite`,
      },
    ],
  },
  {
    slug: 'cli/connectors',
    title: 'Connectors',
    eyebrow: 'CLI',
    group: 'CLI',
    icon: Workflow,
    description:
      'Discover app operations, connect accounts, smoke test payloads, and grant app access safely.',
    blocks: [
      {
        type: 'steps',
        title: 'Connector lifecycle',
        items: [
          'List or inspect the app.',
          'Discover relevant operations or triggers.',
          'Fetch live operation details before writing payloads.',
          'Create a connect request if no account exists.',
          'Execute a realistic smoke test and save the response artifact.',
          'Grant DYNAMIC or FIXED access to the workload.',
          'Only then wire functions, workflows, assistants, or apps.',
        ],
      },
      {
        type: 'code',
        title: 'Discovery and smoke test',
        language: 'bash',
        code: `lemma connector list --limit 100
lemma connector operation-discover gmail --query "send an email"
lemma connector operation-details gmail messages_send
lemma connector account list gmail
lemma connector operation-execute gmail messages_send \
  --account-id <account-id> \
  --payload-file ./payloads/messages-send.json`,
      },
      {
        type: 'table',
        title: 'Access modes',
        columns: ['Mode', 'Use when'],
        rows: [
          ['DYNAMIC', 'The runtime should act through the current caller connected account.'],
          ['FIXED', 'The workload should always use a designated shared or service account.'],
        ],
      },
    ],
  },
  {
    slug: 'guides/build-a-app',
    title: 'Build a App',
    eyebrow: 'Guide',
    group: 'Guides',
    icon: LayoutDashboard,
    description:
      'A practical build checklist for turning pod resources into a real operator workbench.',
    blocks: [
      {
        type: 'steps',
        title: 'App build checklist',
        items: [
          'Write the operator moment: who uses it, what they feel, what must be obvious, what winning looks like.',
          'Name the unit of work and its state transitions.',
          'Choose the scaffold shell that matches the workflow: sidebar, topbar, or single-page.',
          'Rename placeholder routes into real work routes.',
          'Wire lists, detail panels, forms, workflow buttons, and assistant context with SDK hooks.',
          'Use registry blocks only where they match the work object.',
          'Run the app against realistic seeded records.',
          'Deploy only after the main work loop is usable without placeholder UI.',
        ],
      },
      {
        type: 'callout',
        title: 'What good feels like',
        body:
          'The operator opens the app and immediately knows what needs attention, why it matters, and what action is safe to take next.',
      },
    ],
  },
  {
    slug: 'reference/commands',
    title: 'Command Reference',
    eyebrow: 'Reference',
    group: 'Reference',
    icon: Terminal,
    description:
      'A compact map of the CLI command groups currently exposed by lemma --help.',
    blocks: [
      {
        type: 'table',
        title: 'Command groups',
        columns: ['Command', 'Subcommands'],
        rows: [
          ['pod', 'list, create, get, update, delete, config, describe, member-list, member-add, member-update-role, member-remove, export, import'],
          ['table', 'list, create, get, update, delete, describe, column-add, column-remove'],
          ['file', 'list, ls, describe, upload, download, search, get, update, delete, folder-create'],
          ['function', 'list, get, create, update, delete, run, run-list, run-get'],
          ['agent', 'list, get, create, update, delete'],
          ['workflow', 'list, get, create, graph-update, update, delete, install-create, install-list, install-delete, run-start, run-get, run-list, run-resume'],
          ['app', 'list, get, create, update, delete, deploy, clone'],
          ['conversation', 'list, create, get, message-send, message-list'],
          ['connector', 'list, get, operation-discover, operation-details, operation-execute, trigger list/get, account list, connect-request create'],
        ],
      },
    ],
  },
];

export const docsPages: DocsPage[] = [
  ...baseDocsPages,
  howLemmaWorksStub,
  ...conceptDocsPages,
  glossaryStub,
  ...educationGuidePages,
];

export const docsGroups: DocsGroup[] = [
  {
    title: 'Start',
    pages: ['getting-started', 'overview', 'quickstart'],
  },
  conceptDocsGroup,
  {
    title: 'Platform',
    pages: ['platform/pods-and-scope', 'platform/resources', 'platform/data-modeling'],
  },
  {
    title: 'SDK',
    pages: [
      'sdk/installation',
      'sdk/client',
      'sdk/react-auth',
      'sdk/conversations',
      'sdk/data-and-files',
      'sdk/workflows-and-functions',
      'sdk/registry',
    ],
  },
  {
    title: 'CLI',
    pages: [
      'cli/overview',
      'cli/auth-and-context',
      'cli/pods-data-files',
      'cli/functions-agents',
      'cli/workflows',
      'cli/apps',
      'cli/connectors',
    ],
  },
  {
    title: 'Guides',
    pages: [
      'guides/first-agent',
      'guides/inbox-to-table',
      'guides/slack-end-to-end',
      'guides/function-or-agent',
      'guides/sharing-safely',
      'guides/build-a-app',
    ],
  },
  {
    title: 'Reference',
    pages: ['reference/commands'],
  },
];

export const docsPageMap = new Map(docsPages.map((page) => [page.slug, page]));

export function getDocsPage(slug: string): DocsPage | undefined {
  return docsPageMap.get(slug);
}

export function getDocsPageFromSegments(segments: string[] | undefined): DocsPage | undefined {
  const slug = segments?.join('/') || 'overview';
  return getDocsPage(slug);
}

export function getDocsHref(page: DocsPage): string {
  return `/docs/${page.slug}`;
}

export function getAdjacentDocsPages(page: DocsPage): { previous: DocsPage | null; next: DocsPage | null } {
  const index = docsPages.findIndex((item) => item.slug === page.slug);
  return {
    previous: index > 0 ? docsPages[index - 1] : null,
    next: index >= 0 && index < docsPages.length - 1 ? docsPages[index + 1] : null,
  };
}
