export const podBlocks = [
  {
    key: "apps",
    title: "Apps",
    iconKind: "apps",
    count: "2",
    summary: "The software your team opens and uses.",
    detail:
      "Full interfaces for the job, built on everything else in the pod.",
    items: [
      {
        name: "Campaign Manager",
        meta: "Briefs, approvals, calendar, performance",
        state: "Live",
      },
      {
        name: "Codex CRM",
        meta: "Accounts, pipeline, inbox, follow-ups",
        state: "Live",
      },
      {
        name: "Monday Brief",
        meta: "A focused review surface for leadership",
        state: "Internal",
      },
    ],
  },
  {
    key: "agents",
    title: "Agents",
    iconKind: "agents",
    count: "4",
    summary: "AI workers with a specific job and access.",
    detail:
      "Each agent knows what it can read, what it can change, and when to stop.",
    items: [
      {
        name: "Campaign Analyst",
        meta: "Claude · reads campaign data and briefs",
        state: "Working",
      },
      {
        name: "CRM Operator",
        meta: "Codex · researches accounts and drafts replies",
        state: "Working",
      },
      {
        name: "Budget Advisor",
        meta: "Prepares changes · cannot publish them",
        state: "Waiting",
      },
    ],
  },
  {
    key: "workflows",
    title: "Workflows",
    iconKind: "workflows",
    count: "3",
    summary: "The repeatable steps that keep work moving.",
    detail:
      "They connect triggers, agent work, decisions, people, and outside actions.",
    items: [
      {
        name: "Weekly campaign review",
        meta: "Schedule → analysis → approval → publish",
        state: "Waiting",
      },
      {
        name: "Account enrichment",
        meta: "New account → research → update record",
        state: "Running",
      },
      {
        name: "Follow-up queue",
        meta: "Reply signal → draft → human review",
        state: "Ready",
      },
    ],
  },
  {
    key: "data",
    title: "Data",
    iconKind: "data",
    count: "4",
    summary: "The shared records every part of the pod uses.",
    detail:
      "Apps, agents, and workflows read and update the same typed tables.",
    items: [
      {
        name: "campaign_metrics",
        meta: "128 rows · synced 41 seconds ago",
        state: "Healthy",
      },
      {
        name: "accounts",
        meta: "124 records · 8 updated today",
        state: "Healthy",
      },
      {
        name: "decisions",
        meta: "31 records · 2 need review",
        state: "Attention",
      },
    ],
  },
  {
    key: "docs",
    title: "Docs",
    iconKind: "docs",
    count: "3",
    summary: "The context and working files behind the job.",
    detail:
      "People and agents work from the same briefs, policies, and notes.",
    items: [
      {
        name: "Q3 launch brief.md",
        meta: "Used by Campaign Manager + 2 agents",
        state: "Updated",
      },
      {
        name: "Positioning notes.md",
        meta: "Used by Campaign Analyst",
        state: "Current",
      },
      {
        name: "Weekly review.md",
        meta: "Used by Monday review workflow",
        state: "Current",
      },
    ],
  },
  {
    key: "connectors",
    title: "Connectors",
    iconKind: "connectors",
    count: "4",
    summary: "The accounts the pod can use to get work done.",
    detail:
      "Every connection shows the account, its access, and what uses it.",
    items: [
      {
        name: "Google Ads",
        meta: "growth@northstar.co · read campaign data",
        state: "Connected",
      },
      {
        name: "Gmail",
        meta: "maya@northstar.co · read threads, create drafts",
        state: "Connected",
      },
      {
        name: "Slack",
        meta: "Northstar · send approvals and reviewed briefs",
        state: "Connected",
      },
    ],
  },
] as const;

export const surfaceModes = [
  {
    key: "slack",
    label: "Slack",
    caption: "Approvals in channel",
    logos: [{ src: "/landing-page/app-logos/slack.svg", label: "Slack" }],
    headline: "Slack approvals, no extra tab.",
    body: "When a lead like Northwind crosses the line, the approval lands in #sales. Dana approves without leaving Slack - Lemma routes the lead, updates the record, and logs the decision.",
    footnote:
      "Slack is just the surface. The workflow, data, approvals, and connectors live in Lemma.",
  },
  {
    key: "email",
    label: "Gmail",
    caption: "Inbox approvals",
    logos: [{ src: "/landing-page/app-logos/gmail.svg", label: "Gmail" }],
    headline: "Gmail approvals, no inbox sprawl.",
    body: "An email arrives, Lemma drafts the reply from pod context, waits for approval, sends it, and keeps the customer record current.",
    footnote:
      "Gmail is just the surface. Lemma keeps the customer record, workflow state, and approval trail together.",
  },
  {
    key: "outlook",
    label: "Outlook",
    caption: "Mailbox triage",
    logos: [{ src: "/landing-page/app-logos/outlook.svg", label: "Outlook" }],
    headline: "Outlook triage, no manual follow-up.",
    body: "Mailbox threads become structured review work: classify the request, draft the answer, ask the owner, and log the final update.",
    footnote:
      "Outlook is just the surface. The same pod owns the workflow, data updates, and audit trail.",
  },
  {
    key: "teams",
    label: "Teams",
    caption: "Microsoft workspaces",
    logos: [
      { src: "/landing-page/app-logos/teams.svg", label: "Microsoft Teams" },
    ],
    headline: "Teams decisions, no extra dashboard.",
    body: "Lemma can post the summary, collect the decision, route the handoff, and keep Microsoft workspace activity tied to pod state.",
    footnote:
      "Teams is just the surface. The pod still owns the workflow, permissions, and data updates.",
  },
  {
    key: "telegram",
    label: "Telegram",
    caption: "Fast approvals",
    logos: [{ src: "/landing-page/app-logos/telegram.svg", label: "Telegram" }],
    headline: "Telegram approvals, not the system.",
    body: "A quick message can trigger a workflow, ask for the missing decision, and confirm the exact operational change back in chat.",
    footnote:
      "Telegram is just the surface. The pod still decides what changes, who can approve, and what gets logged.",
  },
  {
    key: "whatsapp",
    label: "WhatsApp",
    caption: "Mobile handoffs",
    logos: [{ src: "/landing-page/app-logos/whatsapp.svg", label: "WhatsApp" }],
    headline: "WhatsApp handoffs, without lost state.",
    body: "Field updates, lead routing, and status confirmations can happen on mobile while Lemma keeps ownership and records clean.",
    footnote:
      "WhatsApp is just the surface. The pod still decides what changes, who can approve, and what gets logged.",
  },
  {
    key: "api",
    label: "App + API",
    caption: "Your UI and backend",
    logos: [{ src: "/landing-page/app-logos/api.svg", label: "API" }],
    headline: "API triggers, without custom glue.",
    body: "Use your own UI, webhook, or backend call as the entry point. The same agents, workflows, data, and approvals run behind it.",
    footnote:
      "The API is just the surface. Lemma is the system behind the action.",
  },
] as const;

export type SurfaceMode = (typeof surfaceModes)[number];

export const showcaseCards = [
  {
    tag: "Sales",
    claim: "Automated the entire top of funnel. No SDR. No spreadsheet.",
    flow: "Lead captured -> agent scores ICP -> routed to rep -> sequence drafted -> reply tracked",
  },
  {
    tag: "Support",
    claim: "200 support tickets a day. Zero support hires.",
    flow: "Email arrives -> agent classifies and drafts -> human reviews -> approved -> sent and logged",
  },
  {
    tag: "RevOps",
    claim: "Revenue forecasts that update themselves. Every week.",
    flow: "CRM synced -> agent models pipeline -> forecast updated -> exceptions flagged -> reviewed in app",
  },
  {
    tag: "Finance",
    claim: "Recovered $40k in overdue invoices without awkward emails.",
    flow: "Invoice due -> reminder drafted -> sent via Gmail -> status updated -> escalated if needed",
  },
  {
    tag: "Content",
    claim: "One input. Five content outputs. Twenty minutes of your time.",
    flow: "Topic entered -> sources pulled -> drafts written -> queued for approval -> published",
  },
] as const;

export const githubUrl = "https://github.com/lemma-work/lemma-platform";

export const terminalScript = [
  { command: "uv tool install lemma-terminal", output: [] },
  { command: "lemma pods create support-ops", output: [] },
  { command: "lemma pods import ./support-inbox", output: [] },
  {
    command: "lemma apps deploy support-ops",
    output: [
      "",
      "Created pod: support-ops",
      "Tables: tickets, customers, approvals",
      "Agents: classifier, draft-writer, policy-checker",
      "App: https://support-ops.lemma.work",
    ],
  },
] as const;

export type TerminalLine = { kind: "command" | "output"; text: string };

export const fullTerminalLines: TerminalLine[] = terminalScript.flatMap((step) => [
  { kind: "command" as const, text: step.command },
  ...step.output.map((text) => ({ kind: "output" as const, text })),
]);
