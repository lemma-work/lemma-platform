"use client";

import {
  useEffect,
  useState,
  type ComponentType,
  type ReactNode,
} from "react";
import {
  ArrowRight,
  ArrowUpRight,
  BarChart3,
  Briefcase,
  Calendar,
  Check,
  ChevronDown,
  Clock,
  Database,
  Eye,
  FileText,
  Filter,
  GithubLogo,
  GitMerge,
  Home,
  ListChecks,
  Mail,
  MessageCircle,
  MoreHorizontal,
  Pause,
  Play,
  Plug,
  Plus,
  Search,
  Send,
  Sparkles,
  SquaresFour,
  Table,
  Terminal,
  User,
  Users,
  Zap,
} from "@/components/ui/icons";

type DemoView =
  | "home"
  | "apps"
  | "crm"
  | "campaign"
  | "agents"
  | "workflows"
  | "data"
  | "docs"
  | "connectors";

type AppSection =
  | "campaign-command"
  | "campaign-work"
  | "campaign-calendar"
  | "campaign-assets"
  | "campaign-performance"
  | "crm-overview"
  | "crm-pipeline"
  | "crm-accounts"
  | "crm-inbox"
  | "crm-tasks";

const autoViews: DemoView[] = [
  "campaign",
  "crm",
  "workflows",
  "docs",
  "connectors",
];

const activityEvents = [
  {
    actor: "Claude · Campaign Manager",
    action: "revised the Q3 launch brief",
    target: "2 claims need review",
    tone: "violet",
    icon: Sparkles,
  },
  {
    actor: "Codex · CRM",
    action: "enriched 8 account records",
    target: "Pipeline updated",
    tone: "blue",
    icon: Terminal,
  },
  {
    actor: "Weekly campaign review",
    action: "paused at approval",
    target: "Waiting for Maya",
    tone: "amber",
    icon: GitMerge,
  },
  {
    actor: "campaign_metrics",
    action: "received 18 new rows",
    target: "Google Ads · just now",
    tone: "green",
    icon: Database,
  },
] as const;

const decisions = [
  {
    eyebrow: "SPEND EXCEPTION",
    title: "Meta prospecting is 18% over pace",
    detail: "Claude traced the change to two high-frequency ad sets.",
    state: "Needs review",
    tone: "amber",
  },
  {
    eyebrow: "BUDGET PLAN",
    title: "Move $4,200 to high-intent search",
    detail: "A proposed allocation with projected impact and rollback rules.",
    state: "Approval",
    tone: "violet",
  },
  {
    eyebrow: "WEEKLY BRIEF",
    title: "Monday performance brief is ready",
    detail: "Every claim links back to campaign data and source notes.",
    state: "Ready",
    tone: "green",
  },
] as const;

const crmColumns = [
  {
    label: "Qualified",
    value: "$184k",
    accounts: [
      ["Northwind AI", "$72k", "Codex enriched"],
      ["Meridian Labs", "$64k", "Reply drafted"],
      ["Aster Systems", "$48k", "New signal"],
    ],
  },
  {
    label: "Meeting booked",
    value: "$126k",
    accounts: [
      ["Copper Grid", "$82k", "Tomorrow · 10:30"],
      ["Orbit Health", "$44k", "Friday · 14:00"],
    ],
  },
  {
    label: "Proposal",
    value: "$98k",
    accounts: [
      ["Vesper Cloud", "$98k", "Legal review"],
      ["Luma Works", "$36k", "Follow-up due"],
    ],
  },
] as const;

const agentRows = [
  {
    name: "Campaign Analyst",
    description: "Runs on Claude. Reads briefs, performance data, and notes.",
    state: "Working",
    meta: "Reviewing Q3 launch",
    tone: "violet",
  },
  {
    name: "CRM Operator",
    description: "Runs on Codex. Enriches accounts and prepares follow-ups.",
    state: "Working",
    meta: "8 records updated",
    tone: "blue",
  },
  {
    name: "Report Writer",
    description: "Turns approved evidence into the Monday operating brief.",
    state: "Ready",
    meta: "Brief updated 2 min ago",
    tone: "green",
  },
  {
    name: "Budget Advisor",
    description: "Prepares changes but cannot publish without a person.",
    state: "Waiting",
    meta: "1 decision with Maya",
    tone: "amber",
  },
] as const;

const tableRows = [
  ["Meta · Prospecting", "$18,420", "$72.10", "+18%", "Review"],
  ["Google · Brand", "$6,280", "$24.80", "−6%", "Healthy"],
  ["LinkedIn · Enterprise", "$9,640", "$118.40", "+3%", "Watching"],
  ["Google · Non-brand", "$14,310", "$42.60", "−11%", "Healthy"],
] as const;

const docFiles = [
  {
    id: "launch-brief",
    label: "Q3 launch brief.md",
    folder: "Campaigns",
    icon: FileText,
  },
  {
    id: "positioning",
    label: "Positioning notes.md",
    folder: "Campaigns",
    icon: FileText,
  },
  {
    id: "review-playbook",
    label: "Weekly review.md",
    folder: "Operations",
    icon: FileText,
  },
] as const;

const connectors = [
  {
    id: "google-ads",
    name: "Google Ads",
    account: "growth@northstar.co",
    status: "Connected",
    usedBy: ["Campaign Manager", "Campaign Analyst", "Weekly review"],
    access: "Read campaigns, performance, and conversion data",
    icon: SquaresFour,
    tone: "amber",
  },
  {
    id: "gmail",
    name: "Gmail",
    account: "maya@northstar.co",
    status: "Connected",
    usedBy: ["CRM Operator", "Follow-up queue"],
    access: "Read approved threads and create drafts",
    icon: Mail,
    tone: "red",
  },
  {
    id: "github",
    name: "GitHub",
    account: "northstar-growth",
    status: "Connected",
    usedBy: ["Codex CRM", "CRM Operator"],
    access: "Read issues and open pull requests with approval",
    icon: GithubLogo,
    tone: "ink",
  },
  {
    id: "slack",
    name: "Slack",
    account: "Northstar workspace",
    status: "Connected",
    usedBy: ["Campaign Manager", "Weekly review"],
    access: "Post approvals and reviewed briefs",
    icon: MessageCircle,
    tone: "violet",
  },
] as const;

const workflowSteps = [
  {
    id: "trigger",
    kind: "SCHEDULE",
    title: "Every Monday · 08:00",
    detail: "Starts in the pod timezone.",
    output: "Run 184 started at 08:00:00",
    tone: "neutral",
  },
  {
    id: "query",
    kind: "DATA",
    title: "Load campaign_metrics",
    detail: "Last 7 days compared with plan.",
    output: "128 rows · 6 channels · freshness 41s",
    tone: "green",
  },
  {
    id: "claude",
    kind: "AGENT · CLAUDE",
    title: "Explain movement",
    detail: "Campaign Analyst checks the brief and source data.",
    output: "3 findings · 7 citations · confidence 0.91",
    tone: "violet",
  },
  {
    id: "decision",
    kind: "DECISION",
    title: "Material change?",
    detail: "Routes only meaningful exceptions to a person.",
    output: "Yes · spend variance exceeded 15%",
    tone: "blue",
  },
  {
    id: "approval",
    kind: "HUMAN APPROVAL",
    title: "Maya reviews budget move",
    detail: "The workflow cannot publish the change itself.",
    output: "Waiting · sent to Slack #growth-approvals",
    tone: "amber",
  },
  {
    id: "publish",
    kind: "APP + SLACK",
    title: "Publish Monday brief",
    detail: "Updates Campaign Manager and shares the result.",
    output: "Blocked until approval completes",
    tone: "neutral",
  },
] as const;

function StatusDot({ tone }: { tone: string }) {
  return <i className={`lp-demo-status-dot is-${tone}`} aria-hidden="true" />;
}

function DemoHome() {
  return (
    <div className="lp-demo-home">
      <span className="lp-demo-assist-mark" aria-hidden="true">
        <Sparkles />
      </span>
      <p className="lp-demo-overline">LEMMA ASSIST</p>
      <h3>What should this pod take care of?</h3>
      <p className="lp-demo-home-copy">
        Give it work now, or build an app, agent, or workflow that keeps doing
        it.
      </p>
      <div className="lp-demo-composer">
        <span>Ask Growth Ops to…</span>
        <span className="lp-demo-composer-send" aria-hidden="true">
          <ArrowRight />
        </span>
      </div>
      <div className="lp-demo-quick-actions" aria-label="Example pod actions">
        <span>Build an app</span>
        <span>Automate work</span>
        <span>Create an agent</span>
      </div>
    </div>
  );
}

function DemoApps({
  onOpen,
}: {
  onOpen: (view: "crm" | "campaign") => void;
}) {
  return (
    <div className="lp-demo-page">
      <div className="lp-demo-page-head">
        <div>
          <p className="lp-demo-overline">APPS</p>
          <h3>Software this pod runs</h3>
          <p>
            Interfaces for people, backed by the same agents, data, documents,
            connectors, and approvals.
          </p>
        </div>
        <span className="lp-demo-page-meta">2 deployed</span>
      </div>

      <div className="lp-demo-app-library">
        <button
          className="lp-demo-app-card is-campaign"
          type="button"
          onClick={() => onOpen("campaign")}
        >
          <span className="lp-demo-app-card-head">
            <span className="lp-demo-app-icon">CM</span>
            <span>
              <strong>Campaign Manager</strong>
              <small>Claude · Google Ads · Slack</small>
            </span>
            <em>Live</em>
          </span>
          <span className="lp-demo-app-preview lp-demo-campaign-preview">
            <span>
              <small>Q3 PRODUCT LAUNCH</small>
              <strong>2 decisions need review</strong>
            </span>
            <i />
            <i />
            <i />
          </span>
          <span className="lp-demo-app-card-foot">
            Briefs, approvals, calendar, and live performance
            <ArrowRight />
          </span>
        </button>

        <button
          className="lp-demo-app-card is-crm"
          type="button"
          onClick={() => onOpen("crm")}
        >
          <span className="lp-demo-app-card-head">
            <span className="lp-demo-app-icon">CX</span>
            <span>
              <strong>Codex CRM</strong>
              <small>Codex · Gmail · GitHub</small>
            </span>
            <em>Live</em>
          </span>
          <span className="lp-demo-app-preview lp-demo-crm-preview">
            {["Qualified", "Meeting", "Proposal"].map((column, index) => (
              <span key={column}>
                <small>{column}</small>
                <i />
                <i />
                {index === 0 ? <i /> : null}
              </span>
            ))}
          </span>
          <span className="lp-demo-app-card-foot">
            Accounts, pipeline, meeting prep, and follow-ups
            <ArrowRight />
          </span>
        </button>
      </div>
    </div>
  );
}

const campaignWork = [
  {
    title: "Launch film cutdown",
    channel: "LinkedIn · Video",
    owner: "Claude + Maya",
    state: "Approval",
    due: "Today · 14:00",
    note: "Claude created three variants from the approved launch-film transcript.",
  },
  {
    title: "Enterprise proof carousel",
    channel: "LinkedIn · Organic",
    owner: "Claude",
    state: "In review",
    due: "Today · 17:30",
    note: "Six customer claims are linked to source interviews and proof notes.",
  },
  {
    title: "Founder launch note",
    channel: "Newsletter",
    owner: "Maya",
    state: "Draft",
    due: "Tomorrow · 09:00",
    note: "The working draft uses positioning.md and the approved message hierarchy.",
  },
  {
    title: "Search launch groups",
    channel: "Google Ads",
    owner: "Claude",
    state: "Scheduled",
    due: "Wed · 08:00",
    note: "Budget, keywords, and rollback limits are ready for the launch window.",
  },
] as const;

const crmAccounts = [
  {
    name: "Northwind AI",
    initials: "NA",
    segment: "Enterprise",
    value: "$72k",
    state: "High intent",
    owner: "Maya",
    touch: "Today",
    domain: "northwind.ai",
    signal: "Pricing page visited 4× after the security review.",
  },
  {
    name: "Copper Grid",
    initials: "CG",
    segment: "Enterprise",
    value: "$82k",
    state: "Meeting booked",
    owner: "Dana",
    touch: "12 min",
    domain: "coppergrid.com",
    signal: "Three stakeholders accepted Thursday’s technical review.",
  },
  {
    name: "Meridian Labs",
    initials: "ML",
    segment: "Mid-market",
    value: "$64k",
    state: "Reply drafted",
    owner: "Maya",
    touch: "1 hr",
    domain: "meridianlabs.io",
    signal: "New VP Operations matched the target persona.",
  },
  {
    name: "Vesper Cloud",
    initials: "VC",
    segment: "Enterprise",
    value: "$98k",
    state: "Legal review",
    owner: "Dana",
    touch: "Yesterday",
    domain: "vesper.cloud",
    signal: "MSA question requires a human answer before follow-up.",
  },
] as const;

function ProductAppShell({
  title,
  mark,
  runtime,
  tone,
  sections,
  activeSection,
  onSection,
  children,
}: {
  title: string;
  mark: string;
  runtime: string;
  tone: "violet" | "blue";
  sections: Array<{
    id: AppSection;
    label: string;
    icon: ComponentType;
    badge?: string;
  }>;
  activeSection: AppSection;
  onSection: (section: AppSection) => void;
  children: ReactNode;
}) {
  return (
    <div className={`lp-product-app is-${tone}`}>
      <header className="lp-product-topbar">
        <span className="lp-product-mark">{mark}</span>
        <strong>{title}</strong>
        <span className="lp-product-search">
          <Search />
          Search anything
          <kbd>⌘ K</kbd>
        </span>
        <span className={`lp-demo-runtime is-${tone}`}>
          <i aria-hidden="true" />
          {runtime}
        </span>
        <button type="button" aria-label="More options">
          <MoreHorizontal />
        </button>
        <span className="lp-product-avatar">MK</span>
      </header>
      <aside className="lp-product-sidebar">
        <span className="lp-product-workspace">
          <small>WORKSPACE</small>
          <strong>Q3 Operating Plan</strong>
        </span>
        <nav aria-label={`${title} sections`}>
          {sections.map(({ id, label, icon: Icon, badge }) => (
            <button
              type="button"
              className={activeSection === id ? "is-active" : ""}
              onClick={() => onSection(id)}
              key={id}
            >
              <Icon />
              <span>{label}</span>
              {badge ? <small>{badge}</small> : null}
            </button>
          ))}
        </nav>
        <div className="lp-product-agent">
          <span>
            <Sparkles />
          </span>
          <strong>{tone === "violet" ? "Claude" : "Codex"}</strong>
          <small>{tone === "violet" ? "Monitoring 12 items" : "Working 3 tasks"}</small>
        </div>
      </aside>
      <main className="lp-product-main">{children}</main>
    </div>
  );
}

function ProductPageHeader({
  eyebrow,
  title,
  description,
  action,
}: {
  eyebrow: string;
  title: string;
  description: string;
  action: string;
}) {
  return (
    <div className="lp-product-page-head">
      <div>
        <p>{eyebrow}</p>
        <h3>{title}</h3>
        <span>{description}</span>
      </div>
      <div className="lp-product-page-actions">
        <button type="button">
          <Filter />
          Filter
        </button>
        <button type="button" className="is-primary">
          <Plus />
          {action}
        </button>
      </div>
    </div>
  );
}

function MiniTrend({ variant = "violet" }: { variant?: "violet" | "blue" }) {
  return (
    <span
      className={`lp-product-mini-trend is-${variant}`}
      aria-hidden="true"
    >
      <i /><i /><i /><i /><i /><i /><i /><i />
    </span>
  );
}

function CampaignCommand({
  selectedDecision,
  onDecision,
}: {
  selectedDecision: number;
  onDecision: (index: number) => void;
}) {
  const decision = decisions[selectedDecision];
  return (
    <div className="lp-product-page">
      <ProductPageHeader
        eyebrow="COMMAND CENTER · WEEK 4"
        title="Q3 product launch"
        description="One operating view across creative, distribution, spend, and approvals."
        action="New work"
      />
      <div className="lp-product-kpis">
        {[
          ["Qualified pipeline", "$486k", "+21%", "violet"],
          ["Spend vs plan", "$48.6k", "94%", "blue"],
          ["Content ready", "18 / 24", "3 in review", "green"],
          ["Needs attention", "2", "Human decision", "amber"],
        ].map(([label, value, delta, tone], index) => (
          <article key={label}>
            <span>
              <small>{label}</small>
              <em className={`is-${tone}`}>{delta}</em>
            </span>
            <strong>{value}</strong>
            {index < 2 ? <MiniTrend variant={index === 0 ? "violet" : "blue"} /> : (
              <span className={`lp-product-progress is-${tone}`}>
                <i className={index === 2 ? "is-w75" : "is-w42"} />
              </span>
            )}
          </article>
        ))}
      </div>
      <div className="lp-campaign-command-grid">
        <section className="lp-product-panel lp-campaign-trajectory">
          <header>
            <span>
              <strong>Demand trajectory</strong>
              <small>Qualified pipeline by week</small>
            </span>
            <em>Actual</em>
            <em className="is-muted">Plan</em>
          </header>
          <div className="lp-trajectory-chart">
            <span className="is-grid g1" />
            <span className="is-grid g2" />
            <span className="is-grid g3" />
            <div className="lp-css-chart lp-css-chart-trajectory" aria-hidden="true">
              <i /><i /><i /><i /><i /><i /><i /><i />
            </div>
            <div className="lp-trajectory-axis">
              <span>W1</span><span>W2</span><span>W3</span><span>W4</span>
            </div>
          </div>
        </section>
        <aside className="lp-product-panel lp-claude-brief">
          <header>
            <span className="lp-product-ai-icon"><Sparkles /></span>
            <span><strong>Claude&apos;s brief</strong><small>Updated 6 minutes ago</small></span>
          </header>
          <h4>Enterprise demand is ahead, but paid social efficiency is slipping.</h4>
          <ul>
            <li><i /> Search produced 41% of new qualified demand.</li>
            <li><i /> Meta frequency crossed the campaign guardrail.</li>
            <li><i /> Launch film accounts for 3 of the top 5 journeys.</li>
          </ul>
          <span className="lp-product-source-row">7 sources <ArrowRight /></span>
        </aside>
        <section className="lp-product-panel lp-attention-panel">
          <header>
            <span><strong>Needs attention</strong><small>Decisions only you can make</small></span>
            <button type="button">View all</button>
          </header>
          <div className="lp-attention-list">
            {decisions.slice(0, 2).map((item, index) => (
              <button
                type="button"
                className={selectedDecision === index ? "is-active" : ""}
                onClick={() => onDecision(index)}
                key={item.title}
              >
                <StatusDot tone={item.tone} />
                <span><strong>{item.title}</strong><small>{item.state} · 7 sources</small></span>
                <ArrowRight />
              </button>
            ))}
          </div>
          <div className="lp-attention-detail" key={decision.title}>
            <span><small>{decision.eyebrow}</small><strong>{decision.title}</strong></span>
            <p>{decision.detail}</p>
            <div>
              <button type="button">Open evidence</button>
              <button type="button" className="is-approve"><Check /> Approve</button>
            </div>
          </div>
        </section>
      </div>
    </div>
  );
}

function CampaignWork({
  selected,
  onSelect,
}: {
  selected: number;
  onSelect: (index: number) => void;
}) {
  const work = campaignWork[selected];
  return (
    <div className="lp-product-page">
      <ProductPageHeader
        eyebrow="CONTENT OPERATIONS"
        title="Campaign work"
        description="Briefs, generation, review, and publishing—tracked as one production system."
        action="Create item"
      />
      <div className="lp-product-split-view">
        <section className="lp-product-table-card">
          <header>
            <span>24 items</span>
            <nav><button type="button" className="is-active">All</button><button type="button">Mine</button><button type="button">Waiting</button></nav>
            <button type="button"><Search /></button>
          </header>
          <div className="lp-product-table-row is-head">
            <span>Work</span><span>Owner</span><span>Status</span><span>Due</span>
          </div>
          {campaignWork.map((item, index) => (
            <button
              type="button"
              className={`lp-product-table-row${selected === index ? " is-selected" : ""}`}
              onClick={() => onSelect(index)}
              key={item.title}
            >
              <span><i>{index % 2 ? <FileText /> : <Eye />}</i><strong>{item.title}</strong><small>{item.channel}</small></span>
              <span>{item.owner}</span>
              <span><em className={`is-state s${index}`}>{item.state}</em></span>
              <span>{item.due}</span>
            </button>
          ))}
        </section>
        <aside className="lp-product-record">
          <header>
            <span className="lp-product-record-icon"><FileText /></span>
            <button type="button"><MoreHorizontal /></button>
          </header>
          <small>{work.channel}</small>
          <h4>{work.title}</h4>
          <p>{work.note}</p>
          <dl>
            <div><dt>Owner</dt><dd>{work.owner}</dd></div>
            <div><dt>Due</dt><dd>{work.due}</dd></div>
            <div><dt>Brief</dt><dd>Q3 launch brief ↗</dd></div>
          </dl>
          <div className="lp-product-record-preview">
            <span>V03 · 00:28</span>
            <i className="is-frame one" /><i className="is-frame two" /><i className="is-frame three" />
          </div>
          <div className="lp-product-record-actions">
            <button type="button">Comment</button>
            <button type="button" className="is-primary">Review item</button>
          </div>
        </aside>
      </div>
    </div>
  );
}

function CampaignCalendar() {
  const days = [
    ["MON 18", "Product story", "LinkedIn · Draft", "violet"],
    ["TUE 19", "Founder note", "Newsletter · Review", "amber"],
    ["WED 20", "Launch film", "All channels · Ready", "green"],
    ["THU 21", "Customer proof", "LinkedIn · Planned", "blue"],
    ["FRI 22", "Performance recap", "Slack · Automated", "neutral"],
  ] as const;
  return (
    <div className="lp-product-page">
      <ProductPageHeader
        eyebrow="CALENDAR · JUNE 18–22"
        title="Launch week"
        description="Every item keeps its brief, owner, dependencies, source material, and publish state."
        action="Schedule"
      />
      <div className="lp-product-calendar">
        <div className="lp-product-calendar-times"><span>09:00</span><span>12:00</span><span>15:00</span><span>18:00</span></div>
        {days.map(([day, title, meta, tone], index) => (
          <section className={index === 2 ? "is-today" : ""} key={day}>
            <header><small>{day}</small>{index === 2 ? <em>Today</em> : null}</header>
            <article className={`is-${tone}`}>
              <StatusDot tone={tone} />
              <strong>{title}</strong>
              <small>{meta}</small>
              <span>{index < 2 ? "Maya" : index === 2 ? "Claude + Maya" : "Claude"}</span>
            </article>
            {index === 1 ? <article className="is-light"><strong>Ad QA</strong><small>Google Ads</small></article> : null}
            {index === 3 ? <article className="is-light"><strong>Review window</strong><small>2 approvers</small></article> : null}
          </section>
        ))}
      </div>
    </div>
  );
}

function CampaignAssets() {
  return (
    <div className="lp-product-page">
      <ProductPageHeader
        eyebrow="ASSET LIBRARY"
        title="Approved campaign system"
        description="Current messaging, proof, media, and channel-ready variants."
        action="Upload"
      />
      <div className="lp-asset-layout">
        <aside className="lp-asset-folders">
          <button type="button" className="is-active"><Briefcase /> Launch system <small>38</small></button>
          <button type="button"><FileText /> Messaging <small>8</small></button>
          <button type="button"><Eye /> Creative <small>16</small></button>
          <button type="button"><Users /> Customer proof <small>9</small></button>
          <button type="button"><Table /> Channel kits <small>5</small></button>
        </aside>
        <section className="lp-asset-grid">
          {[
            ["Launch film · master", "VIDEO · 4K", "is-film"],
            ["Enterprise narrative", "DECK · 12 SLIDES", "is-deck"],
            ["Northwind proof", "CUSTOMER STORY", "is-proof"],
            ["Product UI selects", "18 IMAGES", "is-ui"],
            ["Search launch kit", "32 VARIANTS", "is-copy"],
            ["Founder announcement", "APPROVED COPY", "is-note"],
          ].map(([title, meta, className]) => (
            <article key={title}>
              <span className={className}><i /><i /><i /></span>
              <strong>{title}</strong>
              <small>{meta}</small>
              <em><Check /> Approved</em>
            </article>
          ))}
        </section>
      </div>
    </div>
  );
}

function CampaignPerformance({
  range,
  onRange,
}: {
  range: "7d" | "30d";
  onRange: (range: "7d" | "30d") => void;
}) {
  const channels = [
    ["Google Search", "$18.4k", "428", "$43", "+18%"],
    ["LinkedIn", "$12.8k", "196", "$65", "+8%"],
    ["Meta", "$11.2k", "122", "$92", "−14%"],
    ["Newsletter", "$3.6k", "88", "$41", "+25%"],
  ];
  return (
    <div className="lp-product-page">
      <div className="lp-product-page-head">
        <div><p>PERFORMANCE</p><h3>Qualified demand</h3><span>Live channel data reconciled against the operating plan.</span></div>
        <div className="lp-product-segmented">
          {(["7d", "30d"] as const).map((value) => (
            <button type="button" className={range === value ? "is-active" : ""} onClick={() => onRange(value)} key={value}>{value}</button>
          ))}
        </div>
      </div>
      <div className="lp-performance-kpis">
        <article><small>Qualified leads</small><strong>{range === "7d" ? "834" : "2,946"}</strong><em>↑ 21.4%</em></article>
        <article><small>Pipeline created</small><strong>{range === "7d" ? "$486k" : "$1.82m"}</strong><em>↑ 18.2%</em></article>
        <article><small>Cost / qualified</small><strong>{range === "7d" ? "$58" : "$61"}</strong><em className="is-good">↓ 9.6%</em></article>
      </div>
      <div className="lp-performance-layout">
        <section className="lp-product-panel lp-performance-chart">
          <header><span><strong>Qualified demand</strong><small>Actual vs plan</small></span><em>● Actual</em><em className="is-muted">┈ Plan</em></header>
          <div className="lp-performance-plot">
            <span className="is-grid g1" /><span className="is-grid g2" /><span className="is-grid g3" />
            <div className="lp-css-chart lp-css-chart-performance" aria-hidden="true">
              <i /><i /><i /><i /><i /><i /><i /><i /><i /><i />
            </div>
          </div>
        </section>
        <aside className="lp-product-panel lp-performance-insight">
          <span className="lp-product-ai-icon"><Sparkles /></span>
          <small>CLAUDE ANALYSIS</small>
          <h4>Enterprise demand accelerated after the launch film.</h4>
          <p>The effect is concentrated in search and direct visits from target accounts.</p>
          <div><span>Confidence</span><strong>89%</strong></div>
          <button type="button">Open analysis <ArrowRight /></button>
        </aside>
      </div>
      <div className="lp-channel-table">
        <header><span>Channel</span><span>Spend</span><span>Qualified</span><span>CPQ</span><span>vs plan</span></header>
        {channels.map((row, index) => <div key={row[0]}>{row.map((cell, cellIndex) => <span className={cellIndex === 4 ? (index === 2 ? "is-down" : "is-up") : ""} key={cell}>{cell}</span>)}</div>)}
      </div>
    </div>
  );
}

function DemoCampaignManager({
  section,
  onSection,
  selectedDecision,
  onDecision,
}: {
  section: AppSection;
  onSection: (section: AppSection) => void;
  selectedDecision: number;
  onDecision: (index: number) => void;
}) {
  const [selectedWork, setSelectedWork] = useState(0);
  const [range, setRange] = useState<"7d" | "30d">("7d");
  const sections = [
    { id: "campaign-command", label: "Command center", icon: SquaresFour },
    { id: "campaign-work", label: "Campaign work", icon: ListChecks, badge: "4" },
    { id: "campaign-calendar", label: "Calendar", icon: Calendar },
    { id: "campaign-assets", label: "Assets", icon: Briefcase },
    { id: "campaign-performance", label: "Performance", icon: BarChart3 },
  ] satisfies Array<{ id: AppSection; label: string; icon: ComponentType; badge?: string }>;
  return (
    <ProductAppShell title="Campaign Manager" mark="CM" runtime="Claude monitoring" tone="violet" sections={sections} activeSection={section} onSection={onSection}>
      {section === "campaign-command" ? <CampaignCommand selectedDecision={selectedDecision} onDecision={onDecision} /> : null}
      {section === "campaign-work" ? <CampaignWork selected={selectedWork} onSelect={setSelectedWork} /> : null}
      {section === "campaign-calendar" ? <CampaignCalendar /> : null}
      {section === "campaign-assets" ? <CampaignAssets /> : null}
      {section === "campaign-performance" ? <CampaignPerformance range={range} onRange={setRange} /> : null}
    </ProductAppShell>
  );
}

function CrmOverview() {
  return (
    <div className="lp-product-page">
      <ProductPageHeader
        eyebrow="REVENUE COMMAND CENTER"
        title="Good morning, Maya"
        description="Your pipeline is healthy. Three accounts need a human move today."
        action="Add account"
      />
      <div className="lp-product-kpis lp-crm-kpis">
        {[
          ["Open pipeline", "$1.84m", "+14.2%"],
          ["Forecast", "$624k", "82% confidence"],
          ["Meetings", "12", "5 this week"],
          ["Agent hours saved", "38.4h", "+6.2h"],
        ].map(([label, value, delta], index) => (
          <article key={label}><span><small>{label}</small><ArrowUpRight /></span><strong>{value}</strong><em className={index === 1 ? "is-neutral" : ""}>{delta}</em><MiniTrend variant="blue" /></article>
        ))}
      </div>
      <div className="lp-crm-overview-grid">
        <section className="lp-product-panel lp-crm-funnel">
          <header><span><strong>Pipeline movement</strong><small>Last 30 days</small></span><button type="button">All teams <ChevronDown /></button></header>
          {[
            ["Qualified", "$412k", 92],
            ["Meeting booked", "$318k", 73],
            ["Evaluation", "$246k", 58],
            ["Proposal", "$184k", 42],
            ["Commit", "$96k", 24],
          ].map(([label, value, width]) => (
            <div key={label}><span><strong>{label}</strong><small>{value}</small></span><i><em className={`is-w${width}`} /></i></div>
          ))}
        </section>
        <aside className="lp-product-panel lp-crm-today">
          <header><span><strong>Today</strong><small>3 actions · 41 min</small></span><em>Focus</em></header>
          {[
            ["09:30", "Northwind AI", "Review meeting brief", "blue"],
            ["11:00", "Copper Grid", "Approve follow-up", "amber"],
            ["14:30", "Vesper Cloud", "Answer legal question", "violet"],
          ].map(([time, account, action, tone]) => (
            <article key={account}><time>{time}</time><StatusDot tone={tone} /><span><strong>{account}</strong><small>{action}</small></span></article>
          ))}
        </aside>
        <section className="lp-product-panel lp-crm-agent-feed">
          <header><span><strong>Codex activity</strong><small>Work completed from live signals</small></span><span className="lp-demo-runtime is-blue"><i /> working</span></header>
          {[
            ["Enriched 8 buying-committee records", "Gmail + LinkedIn notes · 4 min"],
            ["Prepared Northwind meeting brief", "6 sources · 12 min"],
            ["Drafted follow-up for Copper Grid", "Call transcript + CRM · 18 min"],
          ].map(([title, meta], index) => <article key={title}><span><Zap /></span><div><strong>{title}</strong><small>{meta}</small></div><em>{index === 0 ? "New" : "Ready"}</em></article>)}
        </section>
      </div>
    </div>
  );
}

function CrmPipeline({
  selected,
  onSelect,
}: {
  selected: number;
  onSelect: (index: number) => void;
}) {
  const account = crmAccounts[selected];
  return (
    <div className="lp-product-page">
      <ProductPageHeader eyebrow="PIPELINE · NORTH AMERICA" title="Accounts in motion" description="Live deal state, next actions, and agent-prepared context." action="New opportunity" />
      <div className="lp-crm-pipeline-layout">
        <div className="lp-demo-kanban lp-crm-kanban">
          {crmColumns.map((column) => (
            <section key={column.label}>
              <header><span><strong>{column.label}</strong><small>{column.accounts.length}</small></span><em>{column.value}</em></header>
              {column.accounts.map(([name, value, meta], itemIndex) => {
                const knownIndex = crmAccounts.findIndex((item) => item.name === name);
                const index = knownIndex >= 0
                  ? knownIndex
                  : (itemIndex + column.label.length) % crmAccounts.length;
                return (
                  <button type="button" className={selected === index ? "is-selected" : ""} onClick={() => onSelect(index)} key={name}>
                    <span className="lp-demo-account-mark">{name.split(" ").map((part) => part[0]).join("")}</span>
                    <strong>{name}</strong><span>{value}</span><small>{meta}</small>
                    <i><User /> {index % 2 ? "Dana" : "Maya"}</i>
                  </button>
                );
              })}
            </section>
          ))}
        </div>
        <aside className="lp-crm-deal-drawer">
          <header><span className="lp-demo-account-mark">{account.initials}</span><button type="button"><MoreHorizontal /></button></header>
          <small>OPPORTUNITY</small><h4>{account.name}</h4><a>{account.domain}</a>
          <div className="lp-crm-deal-value"><span><small>Value</small><strong>{account.value}</strong></span><em>{account.state}</em></div>
          <h5>Latest signal</h5><p>{account.signal}</p>
          <h5>Next best action</h5>
          <div className="lp-crm-next-action"><Sparkles /><span><strong>Send the security follow-up</strong><small>Codex prepared a draft from 6 sources.</small></span></div>
          <button type="button" className="lp-crm-open-record">Open account <ArrowRight /></button>
        </aside>
      </div>
    </div>
  );
}

function CrmAccounts({
  selected,
  onSelect,
}: {
  selected: number;
  onSelect: (index: number) => void;
}) {
  const account = crmAccounts[selected];
  return (
    <div className="lp-product-page">
      <ProductPageHeader eyebrow="ACCOUNTS" title="One record, every signal" description="Firmographics, people, correspondence, research, and agent work together." action="Add account" />
      <div className="lp-product-split-view lp-crm-accounts-view">
        <section className="lp-product-table-card">
          <header><span>124 accounts</span><nav><button type="button" className="is-active">All</button><button type="button">Tier 1</button><button type="button">My accounts</button></nav><button type="button"><Search /></button></header>
          <div className="lp-product-table-row is-head"><span>Account</span><span>Owner</span><span>Value</span><span>State</span></div>
          {crmAccounts.map((item, index) => (
            <button type="button" className={`lp-product-table-row${selected === index ? " is-selected" : ""}`} onClick={() => onSelect(index)} key={item.name}>
              <span><i>{item.initials}</i><strong>{item.name}</strong><small>{item.segment}</small></span>
              <span>{item.owner}</span><span>{item.value}</span><span><em className={`is-state s${index}`}>{item.state}</em></span>
            </button>
          ))}
        </section>
        <aside className="lp-account-record">
          <header><span className="lp-demo-account-mark">{account.initials}</span><span><h4>{account.name}</h4><a>{account.domain}</a></span><button type="button"><MoreHorizontal /></button></header>
          <div className="lp-account-chips"><em>{account.segment}</em><em>{account.state}</em><em>{account.value} ARR</em></div>
          <nav><button type="button" className="is-active">Overview</button><button type="button">People</button><button type="button">Activity</button></nav>
          <section><small>LATEST SIGNAL</small><p>{account.signal}</p><em>Detected by Codex · 12 min ago</em></section>
          <dl><div><dt>Owner</dt><dd>{account.owner}</dd></div><div><dt>Last touch</dt><dd>{account.touch}</dd></div><div><dt>Open opportunity</dt><dd>{account.value}</dd></div><div><dt>Buying committee</dt><dd>6 people</dd></div></dl>
          <div className="lp-account-contact"><span>ER</span><span><strong>Elena Rios</strong><small>VP Operations · Champion</small></span><button type="button"><Mail /></button></div>
        </aside>
      </div>
    </div>
  );
}

function CrmInbox() {
  const messages = [
    ["Elena · Northwind AI", "Re: Security review and rollout plan", "Thanks — looping in our IT lead. Thursday works…", "8m", true],
    ["Jordan · Copper Grid", "Technical review follow-up", "The architecture diagram answered the main question…", "42m", true],
    ["Priya · Meridian Labs", "Intro to our new VP Operations", "Maya, meet Tom. He is taking over the evaluation…", "2h", false],
    ["Alex · Vesper Cloud", "MSA question", "Legal needs clarification on the data retention clause…", "1d", false],
  ] as const;
  return (
    <div className="lp-product-page lp-crm-inbox-page">
      <ProductPageHeader eyebrow="SHARED INBOX" title="Revenue conversations" description="Email and CRM context with drafts prepared—but never sent—by Codex." action="Compose" />
      <div className="lp-crm-inbox">
        <aside>
          <header><button type="button" className="is-active">All <small>12</small></button><button type="button">Needs reply <small>4</small></button></header>
          {messages.map(([sender, subject, preview, time, unread], index) => (
            <button type="button" className={`${index === 0 ? "is-active" : ""}${unread ? " is-unread" : ""}`} key={sender}>
              <span>{sender.split(" ")[0].slice(0, 2).toUpperCase()}</span>
              <div><strong>{sender}</strong><em>{time}</em><b>{subject}</b><small>{preview}</small></div>
            </button>
          ))}
        </aside>
        <section>
          <header><span><small>NORTHWIND AI</small><strong>Re: Security review and rollout plan</strong></span><button type="button"><MoreHorizontal /></button></header>
          <article className="lp-crm-message"><span>ER</span><div><strong>Elena Rios <small>elena@northwind.ai</small></strong><p>Thanks — looping in our IT lead. Thursday works for the rollout discussion. Could you also send the data-retention summary before then?</p></div></article>
          <div className="lp-codex-draft"><header><span><Sparkles /></span><strong>Codex prepared a reply</strong><em>6 sources</em></header><p>Hi Elena — absolutely. I&apos;ve attached the data-retention summary and highlighted the controls your IT lead asked about during the review…</p><footer><button type="button">Edit draft</button><button type="button" className="is-primary"><Send /> Review & send</button></footer></div>
        </section>
      </div>
    </div>
  );
}

function CrmTasks() {
  const tasks = [
    ["Review Northwind meeting brief", "Codex prepared from 6 sources", "Maya", "09:30", "Meeting"],
    ["Approve Copper Grid follow-up", "Draft references the call transcript", "Dana", "11:00", "Approval"],
    ["Resolve Vesper legal question", "Human answer required before Codex continues", "Maya", "14:30", "Blocked"],
    ["Confirm Meridian buying committee", "Two contacts need role verification", "Dana", "Tomorrow", "Research"],
  ];
  return (
    <div className="lp-product-page">
      <ProductPageHeader eyebrow="WORK QUEUE" title="What needs a person" description="Decisions, relationships, and actions that agents should not take alone." action="Add task" />
      <div className="lp-crm-task-board">
        <section className="lp-product-panel">
          <header><span><strong>Today</strong><small>3 tasks · 41 min</small></span><button type="button"><Filter /> Priority</button></header>
          {tasks.map(([title, description, owner, time, kind], index) => (
            <article key={title}><button type="button" aria-label={`Complete ${title}`}><Check /></button><span><em>{kind}</em><strong>{title}</strong><small>{description}</small></span><i>{owner}</i><time>{time}</time>{index === 0 ? <b>Open brief <ArrowRight /></b> : null}</article>
          ))}
        </section>
        <aside className="lp-product-panel lp-task-capacity">
          <header><span><strong>Team capacity</strong><small>This week</small></span><em>74%</em></header>
          {[
            ["Maya", "8 / 11 tasks", 72],
            ["Dana", "6 / 8 tasks", 75],
            ["Codex", "24 / 26 tasks", 92],
          ].map(([name, label, width], index) => <div key={name}><span>{index === 2 ? <Terminal /> : <User />}</span><div><strong>{name}</strong><small>{label}</small><i><em className={`is-w${width}`} /></i></div></div>)}
        </aside>
      </div>
    </div>
  );
}

function DemoCodexCrm({
  section,
  onSection,
}: {
  section: AppSection;
  onSection: (section: AppSection) => void;
}) {
  const [selectedAccount, setSelectedAccount] = useState(0);
  const sections = [
    { id: "crm-overview", label: "Overview", icon: SquaresFour },
    { id: "crm-pipeline", label: "Pipeline", icon: Briefcase },
    { id: "crm-accounts", label: "Accounts", icon: Users },
    { id: "crm-inbox", label: "Inbox", icon: Mail, badge: "4" },
    { id: "crm-tasks", label: "Tasks", icon: Check, badge: "3" },
  ] satisfies Array<{ id: AppSection; label: string; icon: ComponentType; badge?: string }>;
  return (
    <ProductAppShell title="Codex CRM" mark="CX" runtime="Codex working" tone="blue" sections={sections} activeSection={section} onSection={onSection}>
      {section === "crm-overview" ? <CrmOverview /> : null}
      {section === "crm-pipeline" ? <CrmPipeline selected={selectedAccount} onSelect={setSelectedAccount} /> : null}
      {section === "crm-accounts" ? <CrmAccounts selected={selectedAccount} onSelect={setSelectedAccount} /> : null}
      {section === "crm-inbox" ? <CrmInbox /> : null}
      {section === "crm-tasks" ? <CrmTasks /> : null}
    </ProductAppShell>
  );
}

function DemoAgents() {
  return (
    <div className="lp-demo-page">
      <div className="lp-demo-page-head">
        <div>
          <p className="lp-demo-overline">AGENTS</p>
          <h3>Workers with a real boundary</h3>
          <p>
            Each one has a runtime, instructions, tools, accessible resources,
            and clear stopping points.
          </p>
        </div>
        <span className="lp-demo-page-meta">2 working · 1 waiting</span>
      </div>
      <div className="lp-demo-agent-list">
        {agentRows.map((agent, index) => (
          <article className="lp-demo-agent" key={agent.name}>
            <span
              className={`lp-demo-agent-mark is-${agent.tone}`}
              aria-hidden="true"
            >
              {index === 1 ? <Terminal /> : <Sparkles />}
            </span>
            <span className="lp-demo-agent-copy">
              <strong>{agent.name}</strong>
              <span>{agent.description}</span>
            </span>
            <span className="lp-demo-agent-meta">
              <em className={index < 2 ? "is-running" : ""}>
                <i aria-hidden="true" />
                {agent.state}
              </em>
              <small>{agent.meta}</small>
            </span>
          </article>
        ))}
      </div>
    </div>
  );
}

function DemoWorkflow({
  selectedStep,
  onStep,
}: {
  selectedStep: number;
  onStep: (index: number) => void;
}) {
  const step = workflowSteps[selectedStep];
  return (
    <div className="lp-demo-page lp-demo-workflow-page">
      <div className="lp-demo-page-head">
        <div>
          <p className="lp-demo-overline">WORKFLOW RUN · 184</p>
          <h3>Weekly campaign review</h3>
          <p>Inspect the actual handoffs, output, and reason this run stopped.</p>
        </div>
        <span className="lp-demo-run-badge">
          <i aria-hidden="true" />
          Waiting for approval
        </span>
      </div>
      <div className="lp-demo-workflow-layout">
        <aside className="lp-demo-workflow-list">
          <small>WORKFLOWS</small>
          <button type="button" className="is-active">
            <GitMerge />
            <span>
              <strong>Weekly campaign review</strong>
              <em>Run 184 · waiting</em>
            </span>
          </button>
          <button type="button">
            <GitMerge />
            <span>
              <strong>Follow-up queue</strong>
              <em>Run 583 · complete</em>
            </span>
          </button>
          <button type="button">
            <GitMerge />
            <span>
              <strong>Account enrichment</strong>
              <em>Run 922 · working</em>
            </span>
          </button>
        </aside>

        <div className="lp-demo-workflow-canvas">
          <span className="lp-demo-wire is-top" />
          <span className="lp-demo-wire is-right" />
          <span className="lp-demo-wire is-middle" />
          <span className="lp-demo-wire is-left" />
          <span className="lp-demo-flow-packet" aria-hidden="true" />
          {workflowSteps.map((item, index) => (
            <button
              type="button"
              className={`lp-demo-canvas-node is-node-${index}${selectedStep === index ? " is-selected" : ""}${index < 4 ? " is-complete" : ""}${index === 4 ? " is-waiting" : ""}`}
              onClick={() => onStep(index)}
              key={item.id}
            >
              <small>{item.kind}</small>
              <strong>{item.title}</strong>
              <span>{index < 4 ? "Complete" : index === 4 ? "Waiting" : "Blocked"}</span>
            </button>
          ))}
        </div>

        <aside className="lp-demo-step-inspector" key={step.id}>
          <span className={`lp-demo-agent-mark is-${step.tone}`}>
            {step.id === "claude" ? <Sparkles /> : null}
            {step.id === "query" ? <Database /> : null}
            {step.id === "trigger" ? <Clock /> : null}
            {step.id === "decision" ? <GitMerge /> : null}
            {step.id === "approval" ? <Users /> : null}
            {step.id === "publish" ? <MessageCircle /> : null}
          </span>
          <small>{step.kind}</small>
          <h4>{step.title}</h4>
          <p>{step.detail}</p>
          <div>
            <small>LAST OUTPUT</small>
            <strong>{step.output}</strong>
          </div>
          <dl>
            <dt>Duration</dt>
            <dd>{selectedStep === 4 ? "Waiting 12m" : selectedStep === 2 ? "48s" : "0.8s"}</dd>
            <dt>Attempt</dt>
            <dd>1 of 3</dd>
          </dl>
        </aside>
      </div>
    </div>
  );
}

function DemoData() {
  return (
    <div className="lp-demo-page">
      <div className="lp-demo-page-head">
        <div>
          <p className="lp-demo-overline">DATA · CAMPAIGN_METRICS</p>
          <h3>Campaign performance</h3>
          <p>Shared state for the app, agents, workflows, and people.</p>
        </div>
        <span className="lp-demo-sync">
          <i aria-hidden="true" />
          Google Ads · synced 41s ago
        </span>
      </div>
      <div className="lp-demo-table-shell">
        <div className="lp-demo-table-tools">
          <span>All campaigns</span>
          <span>128 records · 5 columns</span>
        </div>
        <table>
          <thead>
            <tr>
              <th>Campaign</th>
              <th>Spend</th>
              <th>CPA</th>
              <th>vs plan</th>
              <th>Status</th>
            </tr>
          </thead>
          <tbody>
            {tableRows.map((row, index) => (
              <tr key={row[0]} className={index === 0 ? "is-alert" : ""}>
                {row.map((cell, cellIndex) => (
                  <td key={`${row[0]}-${cellIndex}`}>
                    {cellIndex === 0 ? <i aria-hidden="true" /> : null}
                    {cell}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function DocumentBody({ docId }: { docId: string }) {
  if (docId === "positioning") {
    return (
      <>
        <p className="lp-demo-doc-kicker">CAMPAIGN / POSITIONING</p>
        <h3>Positioning notes</h3>
        <p className="lp-demo-doc-lede">
          The launch should make the operating model visible: people use the
          app; agents work through it.
        </p>
        <h4>What the campaign must establish</h4>
        <ul>
          <li>The app is the working surface, not a generated mockup.</li>
          <li>Every result stays attached to source data and decisions.</li>
          <li>Agents stop at the boundary of human judgment.</li>
        </ul>
        <blockquote>
          Avoid “AI workspace.” Show the specific software the team now has.
        </blockquote>
      </>
    );
  }

  if (docId === "review-playbook") {
    return (
      <>
        <p className="lp-demo-doc-kicker">OPERATIONS / PLAYBOOK</p>
        <h3>Weekly campaign review</h3>
        <p className="lp-demo-doc-lede">
          A repeatable review for signal, spend, message quality, and decisions.
        </p>
        <h4>Before the meeting</h4>
        <ol>
          <li>Refresh channel data and compare against plan.</li>
          <li>Ask Campaign Analyst to explain material movement.</li>
          <li>Queue only decisions that require an owner.</li>
        </ol>
        <h4>Approval policy</h4>
        <p>
          Budget moves above $2,000 or changes to launch claims require Maya.
          Everything else may continue automatically.
        </p>
      </>
    );
  }

  return (
    <>
      <p className="lp-demo-doc-kicker">CAMPAIGN / ACTIVE BRIEF</p>
      <h3>Q3 product launch</h3>
      <p className="lp-demo-doc-lede">
        Introduce the product through the software teams create for work that
        never had the right interface.
      </p>
      <div className="lp-demo-doc-callout">
        <Sparkles />
        <span>
          <strong>Claude is using this brief</strong>
          Campaign Manager · last read 2 min ago
        </span>
      </div>
      <h4>Operating thesis</h4>
      <p>
        Show a campaign moving through a purpose-built app, a scoped agent, live
        data, and one meaningful human approval.
      </p>
      <h4>Launch sequence</h4>
      <ul>
        <li>Lead with the missing software, not the agent.</li>
        <li>Prove that the app and agent share durable state.</li>
        <li>End on the decision a person still owns.</li>
      </ul>
    </>
  );
}

function DemoDocs({
  selectedDoc,
  onDoc,
}: {
  selectedDoc: string;
  onDoc: (docId: string) => void;
}) {
  const selected = docFiles.find((file) => file.id === selectedDoc) ?? docFiles[0];
  return (
    <div className="lp-demo-page lp-demo-doc-page">
      <div className="lp-demo-page-head">
        <div>
          <p className="lp-demo-overline">DOCS</p>
          <h3>{selected.label}</h3>
          <p>Pod knowledge people edit and agents can use with permission.</p>
        </div>
        <span className="lp-demo-page-meta">Saved · shared with pod</span>
      </div>
      <div className="lp-demo-doc-layout">
        <aside className="lp-demo-doc-tree">
          <div className="lp-demo-doc-search">
            <Search />
            Search docs
          </div>
          <small>CAMPAIGNS</small>
          {docFiles
            .filter((file) => file.folder === "Campaigns")
            .map(({ id, label, icon: Icon }) => (
              <button
                type="button"
                className={selectedDoc === id ? "is-active" : ""}
                onClick={() => onDoc(id)}
                key={id}
              >
                <Icon />
                {label}
              </button>
            ))}
          <small>OPERATIONS</small>
          {docFiles
            .filter((file) => file.folder === "Operations")
            .map(({ id, label, icon: Icon }) => (
              <button
                type="button"
                className={selectedDoc === id ? "is-active" : ""}
                onClick={() => onDoc(id)}
                key={id}
              >
                <Icon />
                {label}
              </button>
            ))}
        </aside>
        <article className="lp-demo-document" key={selectedDoc}>
          <DocumentBody docId={selectedDoc} />
        </article>
        <aside className="lp-demo-doc-context">
          <small>USED BY</small>
          <div>
            <span className="lp-demo-agent-mark is-violet">
              <Sparkles />
            </span>
            <span>
              <strong>Campaign Analyst</strong>
              <em>read access</em>
            </span>
          </div>
          <div>
            <span className="lp-demo-agent-mark is-amber">
              <SquaresFour />
            </span>
            <span>
              <strong>Campaign Manager</strong>
              <em>linked brief</em>
            </span>
          </div>
          <div>
            <span className="lp-demo-agent-mark is-blue">
              <GitMerge />
            </span>
            <span>
              <strong>Weekly review</strong>
              <em>workflow input</em>
            </span>
          </div>
          <small>ACTIVITY</small>
          <p>Claude cited this doc in 3 findings.</p>
          <p>Maya edited the launch sequence.</p>
        </aside>
      </div>
    </div>
  );
}

function DemoConnectors({
  selectedConnector,
  onConnector,
}: {
  selectedConnector: string;
  onConnector: (connectorId: string) => void;
}) {
  const selected =
    connectors.find((connector) => connector.id === selectedConnector) ??
    connectors[0];
  const SelectedIcon = selected.icon;

  return (
    <div className="lp-demo-page">
      <div className="lp-demo-page-head">
        <div>
          <p className="lp-demo-overline">CONNECTORS</p>
          <h3>Accounts this pod can use</h3>
          <p>
            Every connector shows the account, granted capability, and resources
            using it.
          </p>
        </div>
        <span className="lp-demo-page-meta">4 connected</span>
      </div>
      <div className="lp-demo-connectors-layout">
        <div className="lp-demo-connector-list">
          {connectors.map(({ id, name, account, status, icon: Icon, tone }) => (
            <button
              type="button"
              className={selectedConnector === id ? "is-selected" : ""}
              onClick={() => onConnector(id)}
              key={id}
            >
              <span className={`lp-demo-connector-icon is-${tone}`}>
                <Icon />
              </span>
              <span>
                <strong>{name}</strong>
                <small>{account}</small>
              </span>
              <em>
                <i aria-hidden="true" />
                {status}
              </em>
              <ArrowRight />
            </button>
          ))}
        </div>
        <aside className="lp-demo-connector-detail" key={selected.id}>
          <span className={`lp-demo-connector-icon is-${selected.tone}`}>
            <SelectedIcon />
          </span>
          <small>CONNECTED ACCOUNT</small>
          <h4>{selected.name}</h4>
          <p>{selected.account}</p>
          <dl>
            <div>
              <dt>Granted access</dt>
              <dd>{selected.access}</dd>
            </div>
            <div>
              <dt>Credential owner</dt>
              <dd>Maya Chen · Growth Ops</dd>
            </div>
          </dl>
          <small>USED BY</small>
          <div className="lp-demo-used-by">
            {selected.usedBy.map((resource) => (
              <span key={resource}>{resource}</span>
            ))}
          </div>
        </aside>
      </div>
    </div>
  );
}

function DemoScreen({
  activeView,
  appSection,
  selectedDecision,
  selectedWorkflowStep,
  selectedDoc,
  selectedConnector,
  onView,
  onAppSection,
  onDecision,
  onWorkflowStep,
  onDoc,
  onConnector,
}: {
  activeView: DemoView;
  appSection: AppSection;
  selectedDecision: number;
  selectedWorkflowStep: number;
  selectedDoc: string;
  selectedConnector: string;
  onView: (view: DemoView) => void;
  onAppSection: (section: AppSection) => void;
  onDecision: (index: number) => void;
  onWorkflowStep: (index: number) => void;
  onDoc: (docId: string) => void;
  onConnector: (connectorId: string) => void;
}) {
  if (activeView === "home") return <DemoHome />;
  if (activeView === "apps") {
    return <DemoApps onOpen={onView} />;
  }
  if (activeView === "campaign") {
    return (
      <DemoCampaignManager
        section={appSection}
        onSection={onAppSection}
        selectedDecision={selectedDecision}
        onDecision={onDecision}
      />
    );
  }
  if (activeView === "crm") {
    return <DemoCodexCrm section={appSection} onSection={onAppSection} />;
  }
  if (activeView === "agents") return <DemoAgents />;
  if (activeView === "workflows") {
    return (
      <DemoWorkflow
        selectedStep={selectedWorkflowStep}
        onStep={onWorkflowStep}
      />
    );
  }
  if (activeView === "data") return <DemoData />;
  if (activeView === "docs") {
    return <DemoDocs selectedDoc={selectedDoc} onDoc={onDoc} />;
  }
  return (
    <DemoConnectors
      selectedConnector={selectedConnector}
      onConnector={onConnector}
    />
  );
}

function isAppsView(view: DemoView) {
  return view === "apps" || view === "crm" || view === "campaign";
}

export function HeroPodDemo() {
  const [activeView, setActiveView] = useState<DemoView>("campaign");
  const [appSection, setAppSection] =
    useState<AppSection>("campaign-command");
  const [activityIndex, setActivityIndex] = useState(0);
  const [selectedDecision, setSelectedDecision] = useState(0);
  const [selectedWorkflowStep, setSelectedWorkflowStep] = useState(4);
  const [selectedDoc, setSelectedDoc] = useState("launch-brief");
  const [selectedConnector, setSelectedConnector] = useState("google-ads");
  const [isLive, setIsLive] = useState(true);

  useEffect(() => {
    const reducedMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
    if (!reducedMotion.matches) return;
    const timer = window.setTimeout(() => setIsLive(false), 0);
    return () => window.clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (!isLive) return;
    const activityTimer = window.setInterval(() => {
      setActivityIndex((current) => (current + 1) % activityEvents.length);
    }, 3400);
    const viewTimer = window.setInterval(() => {
      setActiveView((current) => {
        const currentIndex = autoViews.indexOf(current);
        const next = autoViews[(currentIndex + 1) % autoViews.length];
        if (next === "campaign") setAppSection("campaign-command");
        if (next === "crm") setAppSection("crm-overview");
        return next;
      });
    }, 9000);
    return () => {
      window.clearInterval(activityTimer);
      window.clearInterval(viewTimer);
    };
  }, [isLive]);

  const chooseView = (view: DemoView) => {
    if (view === "campaign") setAppSection("campaign-command");
    if (view === "crm") setAppSection("crm-overview");
    setActiveView(view);
    setIsLive(false);
  };

  const interact = (action: () => void) => {
    action();
    setIsLive(false);
  };

  const activity = activityEvents[activityIndex];
  const ActivityIcon = activity.icon;
  const activeLabel =
    activeView === "campaign"
      ? "Campaign Manager"
      : activeView === "crm"
        ? "Codex CRM"
        : activeView.charAt(0).toUpperCase() + activeView.slice(1);

  const mainNav = [
    { key: "apps", label: "Apps", count: 2, icon: SquaresFour },
    { key: "agents", label: "Agents", count: 4, icon: Sparkles },
    { key: "workflows", label: "Workflows", count: 3, icon: GitMerge },
    { key: "data", label: "Data", count: 4, icon: Database },
    { key: "docs", label: "Docs", count: 3, icon: FileText },
    { key: "connectors", label: "Connectors", count: 4, icon: Plug },
  ] satisfies Array<{
    key: DemoView;
    label: string;
    count: number;
    icon: ComponentType;
  }>;

  return (
    <div className={`lp-demo-shell${isLive ? " is-live" : ""}`}>
      <aside className="lp-demo-sidebar" aria-label="Demo pod navigation">
        <button
          className="lp-demo-pod-switcher"
          type="button"
          onClick={() => chooseView("home")}
        >
          <span>GO</span>
          <strong>
            Growth Ops
            <small>Team pod</small>
          </strong>
          <ChevronDown aria-hidden="true" />
        </button>

        <div className="lp-demo-sidebar-actions">
          <button type="button" onClick={() => chooseView("home")}>
            <Plus />
            New
          </button>
          <button
            type="button"
            className={activeView === "home" ? "is-active" : ""}
            onClick={() => chooseView("home")}
          >
            <Home />
            Home
          </button>
        </div>

        <p className="lp-demo-nav-label">Pod</p>
        <nav className="lp-demo-nav">
          {mainNav.map(({ key, label, count, icon: Icon }) => {
            const isActive =
              key === "apps" ? isAppsView(activeView) : activeView === key;
            return (
              <div className="lp-demo-nav-group" key={key}>
                <button
                  type="button"
                  className={isActive ? "is-active" : ""}
                  aria-pressed={isActive}
                  onClick={() => chooseView(key)}
                >
                  <Icon />
                  <span>{label}</span>
                  <small>{count}</small>
                </button>
                {key === "apps" && isAppsView(activeView) ? (
                  <div className="lp-demo-app-worktree">
                    <button
                      type="button"
                      className={activeView === "campaign" ? "is-active" : ""}
                      onClick={() => chooseView("campaign")}
                    >
                      <i>CM</i>
                      <span>Campaign Manager</span>
                    </button>
                    <button
                      type="button"
                      className={activeView === "crm" ? "is-active" : ""}
                      onClick={() => chooseView("crm")}
                    >
                      <i>CX</i>
                      <span>Codex CRM</span>
                    </button>
                  </div>
                ) : null}
              </div>
            );
          })}
        </nav>

        <div className="lp-demo-sidebar-foot">
          <span className="lp-demo-person">M</span>
          <span>
            <strong>Maya Chen</strong>
            <small>Growth Ops</small>
          </span>
        </div>
      </aside>

      <div className="lp-demo-workspace">
        <header className="lp-demo-tabbar">
          <div className="lp-demo-tabs">
            <button
              type="button"
              className={activeView === "home" ? "is-active" : ""}
              onClick={() => chooseView("home")}
            >
              <Home />
              Home
            </button>
            {activeView !== "home" ? (
              <span className="is-active">
                {isAppsView(activeView) ? <SquaresFour /> : null}
                {activeView === "agents" ? <Sparkles /> : null}
                {activeView === "workflows" ? <GitMerge /> : null}
                {activeView === "data" ? <Database /> : null}
                {activeView === "docs" ? <FileText /> : null}
                {activeView === "connectors" ? <Plug /> : null}
                {activeLabel}
              </span>
            ) : null}
          </div>
          <span className="lp-demo-running">
            <i aria-hidden="true" />
            3 active
          </span>
        </header>

        <main className="lp-demo-main" key={activeView}>
          <DemoScreen
            activeView={activeView}
            appSection={appSection}
            selectedDecision={selectedDecision}
            selectedWorkflowStep={selectedWorkflowStep}
            selectedDoc={selectedDoc}
            selectedConnector={selectedConnector}
            onView={chooseView}
            onAppSection={(section) =>
              interact(() => setAppSection(section))
            }
            onDecision={(index) =>
              interact(() => setSelectedDecision(index))
            }
            onWorkflowStep={(index) =>
              interact(() => setSelectedWorkflowStep(index))
            }
            onDoc={(docId) => interact(() => setSelectedDoc(docId))}
            onConnector={(connectorId) =>
              interact(() => setSelectedConnector(connectorId))
            }
          />
        </main>

        <footer className="lp-demo-livebar">
          <span
            className={`lp-demo-activity-icon is-${activity.tone}`}
            aria-hidden="true"
          >
            <ActivityIcon />
          </span>
          <p key={activity.actor}>
            <strong>{activity.actor}</strong>
            <span>{activity.action}</span>
          </p>
          <em>{activity.target}</em>
          <span className="lp-demo-live-label">
            <i aria-hidden="true" />
            {isLive ? "Live" : "Paused"}
          </span>
          <button
            type="button"
            aria-label={isLive ? "Pause live demo" : "Play live demo"}
            onClick={() => setIsLive((current) => !current)}
          >
            {isLive ? <Pause /> : <Play />}
          </button>
          <span
            className="lp-demo-live-progress"
            key={`${activityIndex}-${activeView}`}
          >
            <i />
          </span>
        </footer>
      </div>
    </div>
  );
}
