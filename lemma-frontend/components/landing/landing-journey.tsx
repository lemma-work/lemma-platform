"use client";

import Image from "next/image";

/**
 * §3 — the actual path a pod takes: built somewhere, deployed, opened up to
 * people, scoped, and then reached from wherever those people already are.
 * Each layer shows the real product surface for that step rather than a
 * narrated story, because the depth is the point.
 */

function BuildMock() {
  return (
    <div className="lp-jr-mock lp-jr-build" aria-hidden="true">
      <div className="lp-jr-sources">
        {[
          { label: "Claude Code", src: "/harnesslogos/claudecode.png" },
          { label: "Codex", src: "/harnesslogos/codex.png" },
          { label: "Cursor", src: "/harnesslogos/cursor.png" },
        ].map((source) => (
          <span key={source.label}>
            <Image src={source.src} alt="" width={16} height={16} unoptimized />
            {source.label}
          </span>
        ))}
        <span className="is-lemma">or in Lemma</span>
      </div>

      <div className="lp-jr-term">
        <header>
          <i />
          <i />
          <i />
          <small>support-ops</small>
        </header>
        <p>
          <b>$</b> lemma pod import ./support-ops
        </p>
        {[
          ["tables", "tickets, customers, refunds"],
          ["agents", "classifier, draft-writer"],
          ["workflows", "refund-review"],
          ["app", "support-ops"],
        ].map(([kind, detail]) => (
          <p className="is-ok" key={kind}>
            <em>✓</em>
            <span>{kind}</span>
            <small>{detail}</small>
          </p>
        ))}
      </div>
    </div>
  );
}

function DeployMock() {
  return (
    <div className="lp-jr-mock lp-jr-deploy" aria-hidden="true">
      <div className="lp-jr-app-card">
        <header>
          <strong>Support Ops</strong>
          <span className="lp-jr-dot" />
          <small>Live</small>
        </header>
        <p className="lp-jr-url">support-ops.lemma.work</p>
        <div className="lp-jr-app-meta">
          <span>
            <b>v1.4.2</b>deployed 2m ago
          </span>
          <span>
            <b>3</b>agents attached
          </span>
        </div>
      </div>

      <div className="lp-jr-agents">
        {[
          ["Classifier", "on new ticket", "Working", "green"],
          ["Draft writer", "on classify done", "Working", "green"],
          ["Policy checker", "waiting for approval", "Paused", "yellow"],
        ].map(([name, trigger, state, tone]) => (
          <p key={name}>
            <span className="lp-jr-agent-mark">AI</span>
            <span>
              <strong>{name}</strong>
              <small>{trigger}</small>
            </span>
            <b className={`lp-pill ${tone}`}>{state}</b>
          </p>
        ))}
      </div>
    </div>
  );
}

function InviteMock() {
  return (
    <div className="lp-jr-mock lp-jr-invite" aria-hidden="true">
      <header className="lp-jr-panel-head">
        <strong>Members</strong>
        <small>4 people · 3 agents</small>
      </header>
      {[
        ["DA", "Dana", "dana@northstar.co", "Owner", "purple"],
        ["MC", "Maya Chen", "maya@northstar.co", "Member", "grey"],
        ["JP", "Jordan P.", "jordan@northstar.co", "Member", "grey"],
        ["RS", "Riley S.", "invite sent", "Pending", "yellow"],
      ].map(([initials, name, email, role, tone]) => (
        <p key={name}>
          <span className={`lp-jr-avatar is-${tone}`}>{initials}</span>
          <span>
            <strong>{name}</strong>
            <small>{email}</small>
          </span>
          <b className={`lp-pill ${tone}`}>{role}</b>
        </p>
      ))}
      <footer className="lp-jr-invite-foot">
        <span>Invite by link</span>
        <em>lemma.work/j/support-ops</em>
      </footer>
    </div>
  );
}

const ACCESS_ROWS = [
  { who: "Dana", kind: "Owner", tickets: "edit", customers: "edit", refunds: "approve" },
  { who: "Maya", kind: "Member", tickets: "edit", customers: "read", refunds: "request" },
  { who: "Draft writer", kind: "Agent", tickets: "edit", customers: "read", refunds: "none" },
  { who: "Classifier", kind: "Agent", tickets: "read", customers: "none", refunds: "none" },
] as const;

const ACCESS_TONE: Record<string, string> = {
  edit: "green",
  approve: "purple",
  read: "grey",
  request: "yellow",
  none: "red",
};

function AccessMock() {
  return (
    <div className="lp-jr-mock lp-jr-access" aria-hidden="true">
      <header className="lp-jr-panel-head">
        <strong>Access</strong>
        <small>people and agents, same rules</small>
      </header>
      <div className="lp-jr-matrix">
        <div className="lp-jr-matrix-head">
          <span />
          <span>tickets</span>
          <span>customers</span>
          <span>refunds</span>
        </div>
        {ACCESS_ROWS.map((row) => (
          <div className="lp-jr-matrix-row" key={row.who}>
            <span>
              <strong>{row.who}</strong>
              <small>{row.kind}</small>
            </span>
            {[row.tickets, row.customers, row.refunds].map((value, index) => (
              <span key={`${row.who}-${index}`}>
                <b className={`lp-pill ${ACCESS_TONE[value]}`}>{value}</b>
              </span>
            ))}
          </div>
        ))}
      </div>
    </div>
  );
}

function UseMock() {
  return (
    <div className="lp-jr-mock lp-jr-use" aria-hidden="true">
      <div className="lp-jr-use-row">
        <article className="lp-jr-use-card">
          <header>
            <Image
              src="/landing-page/app-logos/telegram.svg"
              alt=""
              width={15}
              height={15}
            />
            Telegram
          </header>
          <p className="them">Any refunds waiting on me?</p>
          <p className="me">One. $420, Northwind.</p>
        </article>

        <article className="lp-jr-use-card">
          <header>
            <span className="lp-jr-gpt">AI</span>
            ChatGPT
          </header>
          <p className="them">Summarize this week&apos;s refunds.</p>
          <p className="me">14 approved, 2 pending review.</p>
        </article>

        <article className="lp-jr-use-card is-app">
          <header>
            <span className="lp-jr-appmark" />
            The app
          </header>
          <div className="lp-jr-use-rows">
            <i />
            <i />
            <i className="short" />
          </div>
          <span className="lp-jr-use-cta">Open Support Ops</span>
        </article>
      </div>

      <p className="lp-jr-use-base">
        <span />
        One pod · one set of records · the same permissions everywhere
      </p>
    </div>
  );
}

const LAYERS = [
  {
    key: "build",
    step: "01",
    title: "Build it where you already work.",
    body: "Claude Code, Codex, Cursor, OpenCode — or inside Lemma itself. The agent authors the tables, agents, workflows, permissions, and the app, then verifies them through the same CLI.",
    depth: ["Any coding agent", "Authored as plain files", "Verified by the agent that wrote it"],
    Mock: BuildMock,
  },
  {
    key: "deploy",
    step: "02",
    title: "The app and its agents go live together.",
    body: "One URL for the people who use it. The agents start working on schedules, webhooks, and table events — not only when someone is watching.",
    depth: ["Deployed at a URL", "Agents run in the background", "Versioned and rollback-able"],
    Mock: DeployMock,
  },
  {
    key: "invite",
    step: "03",
    title: "Bring your team. And anyone else who needs it.",
    body: "Teammates, clients, friends. They get working software, not a repository — an invite link and the app, with their own account inside the pod.",
    depth: ["Invite by link or email", "Teammates, clients, guests", "Humans and agents are both members"],
    Mock: InviteMock,
  },
  {
    key: "access",
    step: "04",
    title: "Decide exactly what each one can touch.",
    body: "The same permission model covers people and agents. Grant a table, hide a resource, or require an approval before a consequential action goes through.",
    depth: ["Per-table grants", "Resource visibility", "Approval gates on the risky steps"],
    Mock: AccessMock,
  },
  {
    key: "use",
    step: "05",
    title: "Use it from wherever you already are.",
    body: "Telegram, ChatGPT, Slack, email — or open it as an app. Every entry point reads and writes the same records and respects the same permissions.",
    depth: ["Chat surfaces", "Your own UI or API", "The app itself"],
    Mock: UseMock,
  },
] as const;

export function JourneySection() {
  return (
    <section className="lp-section lp-journey-section" id="loop">
      <div className="lp-section-inner">
        <div className="lp-journey-head lp-reveal">
          <p className="lp-section-kicker">From a prompt to running team software</p>
          <h2 className="lp-section-title">
            Build it, ship it, <span>and hand it to your team.</span>
          </h2>
          <p className="lp-section-subhead">
            Five layers. All of it real.
          </p>
        </div>

        <div className="lp-journey-layers">
          {LAYERS.map(({ key, step, title, body, depth, Mock }) => (
            <article className={`lp-jr-layer is-${key} lp-reveal`} key={key}>
              <div className="lp-jr-copy">
                <p className="lp-jr-step">{step}</p>
                <h3>{title}</h3>
                <p className="lp-jr-body">{body}</p>
                <ul className="lp-jr-depth">
                  {depth.map((item) => (
                    <li key={item}>{item}</li>
                  ))}
                </ul>
              </div>
              <div className="lp-jr-stage">
                <Mock />
              </div>
            </article>
          ))}
        </div>
      </div>
    </section>
  );
}
