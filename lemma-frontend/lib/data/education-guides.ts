import { Bot, GitBranch, MessageCircle, Scale, ShieldCheck } from '@/components/ui/icons';

import type { DocsPage } from '@/lib/data/docs';

/**
 * Task guides for product users working in the app UI (not the CLI — the CLI
 * has its own section). Each one walks a real outcome end to end.
 */
export const educationGuidePages: DocsPage[] = [
  {
    slug: 'guides/first-agent',
    title: 'Your first agent that actually does something',
    eyebrow: 'Guide',
    group: 'Guides',
    icon: Bot,
    description:
      'From a blank pod to an agent that reads real inbound work and writes real records — in about fifteen minutes.',
    blocks: [
      {
        type: 'paragraph',
        title: 'The trap to avoid',
        body:
          'The most common failed first agent is a general assistant with access to everything and a vague brief. It demos well and then nobody trusts it with real work. The fix is to go narrow: one job, one table, one example you can check by hand.',
      },
      {
        type: 'steps',
        title: 'Build it',
        items: [
          'Pick one judgment-heavy step your team repeats — labeling inbound requests is the classic. Write down three real examples and what a good output looks like for each.',
          'Create the table first (Data → new table). Give it the columns your output needs: title, category, priority, status with a default of OPEN.',
          'Create the agent (Agents → New agent). Instructions are a job description: what arrives, what to produce, what to do when unsure. Paste one of your real examples into the instructions as a worked sample.',
          'Open the Access section and grant exactly one thing: write access to your new table. Resist granting more.',
          'Run it from chat with your second real example. Open the conversation and expand the tool calls — check the row it wrote.',
          'Run the third example. If both rows are right, wire it to a surface or a workflow. If not, fix the instructions, not the access.',
        ],
      },
      {
        type: 'callout',
        tone: 'success',
        title: 'You know it worked when',
        body:
          'Someone on your team sees the rows the agent wrote and cannot tell which were created by a person.',
      },
      {
        type: 'list',
        title: 'When the output is wrong',
        items: [
          'Vague labels → add the label list to the instructions with one example per label.',
          'Made-up fields → tighten the table columns; agents follow schemas better than prose.',
          'Right answer, wrong format → show the exact output you want inside the instructions.',
          'Confidently wrong on edge cases → tell it what to do when unsure: flag, don’t guess.',
        ],
      },
    ],
  },
  {
    slug: 'guides/inbox-to-table',
    title: 'From inbox to table: a triage flow',
    eyebrow: 'Guide',
    group: 'Guides',
    icon: GitBranch,
    description:
      'Turn a shared inbox into structured, assigned work: a workflow that reads each message, files a record, and routes the urgent ones to a person.',
    blocks: [
      {
        type: 'paragraph',
        title: 'The shape of triage',
        body:
          'Every triage process is the same five steps: something arrives, judgment classifies it, rules score it, a record is filed, and a human catches the exceptions. That maps one-to-one onto Lemma: surface → agent → function → table → approval.',
      },
      {
        type: 'steps',
        title: 'Build it',
        items: [
          'Prepare the pieces: a tickets table (Data), a triage agent that classifies one message (see the first-agent guide), and optionally a scoring function if you have exact priority rules.',
          'Open Workflows and create one. First step: the triage agent, taking the inbound message as input.',
          'Second step: a function if your priority rules are exact ("enterprise customers are always P1"), or skip — the agent’s judgment may be enough to start.',
          'Third step: a decision — P1 goes one way, everything else goes the other.',
          'On the P1 branch, add an approval routed to the on-call person. On the other branch, the flow just files the record and ends.',
          'Run it by hand with a real message and read the run step by step. Then connect the surface (Surfaces → your inbox) so it triggers on arrival.',
        ],
      },
      {
        type: 'callout',
        tone: 'warning',
        title: 'Start the surface last',
        body:
          'Wire the inbox trigger only after manual runs look right. A flow misfiling test data is a shrug; a flow misfiling a week of real customer email is a cleanup project.',
      },
    ],
  },
  {
    slug: 'guides/slack-end-to-end',
    title: 'Connecting Slack, end to end',
    eyebrow: 'Guide',
    group: 'Guides',
    icon: MessageCircle,
    description:
      'From "nothing connected" to an agent answering in a channel and approvals landing in Slack — with the routing rules explained.',
    blocks: [
      {
        type: 'steps',
        title: 'Connect it',
        items: [
          'Open Surfaces and turn on Slack. You will authorize a workspace — use an account that can add apps to the channels you care about.',
          'Pick the agent that handles inbound Slack work. One agent per surface keeps behavior predictable.',
          'Set routing: which channels the pod listens to, and whether it responds to every message or only mentions. Start with mentions-only in one channel.',
          'In Slack, mention the bot with a real request. Watch the reply arrive in-channel, then open Conversations in Lemma to see the same thread with every tool call visible.',
          'If a workflow has approval steps, assignees with connected Slack get those approvals as messages — approve without leaving the channel.',
        ],
      },
      {
        type: 'list',
        title: 'Routing rules of thumb',
        items: [
          'Mentions-only in shared channels; every-message only in dedicated channels like #support-inbox.',
          'Separate noisy intake (every message) from collaboration channels (mentions) using two routing rules.',
          'The agent answers with its scoped knowledge — if it should know your policies, grant it the policies folder.',
        ],
      },
      {
        type: 'callout',
        tone: 'note',
        title: 'Surfaces vs connectors',
        body:
          'Connecting Slack as a surface lets work reach the pod. Connecting Slack as a connector lets agents and flows act on Slack — post to channels, read history — as a tool. Many pods use both.',
      },
    ],
  },
  {
    slug: 'guides/function-or-agent',
    title: 'When to write a function instead of an agent',
    eyebrow: 'Guide',
    group: 'Guides',
    icon: Scale,
    description:
      'The judgment-versus-rules decision, with real examples of each and the hybrid pattern that uses both.',
    blocks: [
      {
        type: 'paragraph',
        title: 'The one-question test',
        body:
          'Could two careful colleagues disagree about the right output? If yes, it is judgment — use an agent. If no — if there is one correct answer a rulebook produces — it is rules, and rules belong in a function, where the same input gives the same output every single time.',
      },
      {
        type: 'table',
        title: 'Sorting real steps',
        columns: ['Step', 'Verdict', 'Why'],
        rows: [
          ['Summarize this support thread', 'Agent', 'No single correct summary exists.'],
          ['Score a lead by employee count and region', 'Function', 'The rulebook is exact; disagreement means a bug.'],
          ['Decide if this email is a complaint', 'Agent', 'Language is ambiguous; judgment required.'],
          ['Validate an expense against policy limits', 'Function', 'Limits are numbers; the answer is yes or no.'],
          ['Draft the rejection message', 'Agent', 'Tone and content vary by case.'],
          ['Convert currency on the invoice', 'Function', 'Math. Always math.'],
        ],
      },
      {
        type: 'paragraph',
        title: 'The hybrid pattern',
        body:
          'Most good workflows sandwich them: an agent interprets the messy input, a function applies the exact rules to the agent’s structured output, and another agent drafts the human-facing result. Judgment at the edges, rules in the middle.',
      },
      {
        type: 'callout',
        tone: 'warning',
        title: 'The smell to watch for',
        body:
          'If you find yourself writing agent instructions full of thresholds, point values, and if-then rules, stop — that paragraph wants to be a function. Agents drift; functions don’t.',
      },
    ],
  },
  {
    slug: 'guides/sharing-safely',
    title: 'Sharing safely',
    eyebrow: 'Guide',
    group: 'Guides',
    icon: ShieldCheck,
    description:
      'The two permission systems — sharing and access scopes — and how to set up a pod where the right people and the right agents see the right things.',
    blocks: [
      {
        type: 'paragraph',
        title: 'Two systems, not one',
        body:
          'Lemma separates who can see a resource from what a worker can touch. Sharing (the Share button) controls people: private, pod members, or the whole organization. Access scopes (the Access section in an agent’s editor) control workers: which tables, folders, tools, and apps that agent may read and act on. Confusing them is the most common permissions mistake — a pod-visible agent can still be scoped to a single table, and a private table can still be granted to an agent everyone uses.',
      },
      {
        type: 'steps',
        title: 'A sane default setup',
        items: [
          'Keep resources at pod visibility while you build — drafts stay private until they work.',
          'Scope every agent from zero: grant only what its job description mentions.',
          'Put genuinely sensitive tables (salaries, legal) at private visibility and grant them to no agent unless the process demands it.',
          'Promote finished apps to organization visibility when other teams should use them — the app shows data through its own queries, not the viewer’s permissions, so check what it exposes first.',
          'Audit quarterly: open each agent’s Access section and remove grants its current job no longer needs.',
        ],
      },
      {
        type: 'table',
        title: 'Who sees what',
        columns: ['Level', 'Who', 'Use for'],
        rows: [
          ['Private', 'Only you', 'Drafts, experiments, sensitive tables'],
          ['Pod', 'Members of this pod', 'The default for everything in active use'],
          ['Organization', 'Anyone in your org', 'Finished apps and shared reference data'],
        ],
      },
      {
        type: 'callout',
        tone: 'note',
        title: 'Approvals complete the picture',
        body:
          'Sharing and scopes control standing access. For one-off consequential actions — refunds, sends, deletes — use an approval step in the workflow so a named person decides each time.',
      },
    ],
  },
];

export const educationGuideSlugs = educationGuidePages.map((page) => page.slug);
