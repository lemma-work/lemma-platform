import { GITHUB_REPO_URL } from "@/lib/community-links";

export const surfaceModes = [
  {
    key: "slack",
    effect: ["tickets · lead routed to enterprise", "decisions · logged with approver", "workflow · resumed"],
    label: "Slack",
    caption: "Team decisions",
    logos: [{ src: "/landing-page/app-logos/slack.svg", label: "Slack" }],
    headlineLead: "The decision happens",
    headlineTail: "in the channel.",
    body: "The pod posts what needs a call, with the evidence attached. Someone answers in #sales, and that answer is the state change — the lead routes, the record updates, the decision is logged.",
  },
  {
    key: "chatgpt",
    effect: ["refunds · 5 rows read", "refund.approve · blocked, needs a person", "nothing written it lacked rights to"],
    label: "ChatGPT",
    caption: "Ask your pod",
    logos: [{ src: "/landing-page/app-logos/chatgpt.svg", label: "ChatGPT" }],
    headlineLead: "Your pod,",
    headlineTail: "inside ChatGPT.",
    body: "Connect the pod and ChatGPT can query your real tables, run your workflows, and act through your connectors — under the same permissions as everyone else. It cannot read what it was not granted.",
  },
  {
    key: "claude",
    effect: ["refund-review · 3 runs resumed", "tickets · 3 rows closed", "2 held at the approval gate"],
    label: "Claude",
    caption: "Work the pod",
    logos: [{ src: "/landing-page/app-logos/claude.svg", label: "Claude" }],
    headlineLead: "Claude works",
    headlineTail: "the same pod.",
    body: "Not a copy of your data in a chat window. Claude reads and writes the pod's records directly, and stops at the approval gates you set — the same ones that apply to your team.",
  },
  {
    key: "telegram",
    effect: ["captures · 1 record created", "files · voice note transcribed", "workflow · triage started"],
    label: "Telegram",
    caption: "A pod in your pocket",
    logos: [{ src: "/landing-page/app-logos/telegram.svg", label: "Telegram" }],
    headlineLead: "Send a message,",
    headlineTail: "get a state change.",
    body: "A note, a photo, a voice message. It lands as a structured record, the workflow picks it up, and you get back the exact thing that changed — not just an acknowledgement.",
  },
  {
    key: "whatsapp",
    effect: ["jobs · status set to on-site", "photos · attached to the record", "owner · unchanged"],
    label: "WhatsApp",
    caption: "Field updates",
    logos: [{ src: "/landing-page/app-logos/whatsapp.svg", label: "WhatsApp" }],
    headlineLead: "Work from the field,",
    headlineTail: "without losing the thread.",
    body: "Updates, handoffs, and confirmations from a phone. Ownership, history, and records stay clean in the pod while the conversation stays where your people already are.",
  },
  {
    key: "email",
    effect: ["tickets · draft written", "customers · history read", "send · waiting on you"],
    label: "Gmail",
    caption: "Inbox as input",
    logos: [{ src: "/landing-page/app-logos/gmail.svg", label: "Gmail" }],
    headlineLead: "Your inbox",
    headlineTail: "becomes an input.",
    body: "Mail arrives and the pod reads it: classify the request, pull the customer's history, draft the reply. It waits for you before sending, and the record is current either way.",
  },
  {
    key: "outlook",
    effect: ["requests · classified", "policy · checked", "owner · notified for sign-off"],
    label: "Outlook",
    caption: "Mailbox triage",
    logos: [{ src: "/landing-page/app-logos/outlook.svg", label: "Outlook" }],
    headlineLead: "Mailbox threads,",
    headlineTail: "turned into work.",
    body: "Every thread becomes something reviewable: what was asked, what the policy says, what the answer should be, and who has to sign off before it goes out.",
  },
  {
    key: "teams",
    effect: ["campaigns · spend paused", "decisions · logged", "finance · notified"],
    label: "Teams",
    caption: "Where the org meets",
    logos: [
      { src: "/landing-page/app-logos/teams.svg", label: "Microsoft Teams" },
    ],
    headlineLead: "Decisions where",
    headlineTail: "the org already meets.",
    body: "The pod brings the summary and the choice into the workspace, collects the decision, routes the handoff, and keeps the underlying records in step.",
  },
  {
    key: "api",
    effect: ["any table · read or written", "any workflow · triggered", "same permissions · enforced"],
    label: "App + API",
    caption: "Your UI and backend",
    logos: [{ src: "/landing-page/app-logos/api.svg", label: "API" }],
    headlineLead: "Your own front end,",
    headlineTail: "same system underneath.",
    body: "Your UI, a webhook, or a backend call as the entry point. The same agents, workflows, records, and approvals run behind it — no glue code to maintain.",
  },
] as const;

export type SurfaceMode = (typeof surfaceModes)[number];

export const githubUrl = GITHUB_REPO_URL;

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
