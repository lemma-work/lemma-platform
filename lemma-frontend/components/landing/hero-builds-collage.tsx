"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { ArrowRight } from "@/components/ui/icons";
import { LemmaMark } from "@/components/brand/logo";

type TileKey =
  | "slack"
  | "telegram"
  | "tools"
  | "codex"
  | "portal"
  | "inbox"
  | "approvals";

interface BuildTile {
  key: TileKey;
  title: string;
  philosophy: string;
  examples: string[];
}

const BUILD_TILES: BuildTile[] = [
  {
    key: "slack",
    title: "Slack agents",
    philosophy: "Meet your team where it already works.",
    examples: ["Incident coordinator", "Standup digest", "Knowledge teammate"],
  },
  {
    key: "telegram",
    title: "Telegram agent + mini app",
    philosophy: "Chat is the surface. The pod is the system.",
    examples: ["Deploy logbook", "Shop mini app", "Community concierge"],
  },
  {
    key: "tools",
    title: "Internal tools",
    philosophy: "The exact tool you need doesn\u2019t exist. Build it before lunch.",
    examples: ["On-call board", "Release checklist", "Cost explorer"],
  },
  {
    key: "codex",
    title: "Skins for Codex",
    philosophy: "Same agent. A new uniform for every job.",
    examples: ["Release operator", "Repo investigator", "Pod builder"],
  },
  {
    key: "portal",
    title: "Client portals",
    philosophy: "Give clients a window into the work, not a seat in the mess.",
    examples: ["Project health", "Request tracker", "Milestone map"],
  },
  {
    key: "inbox",
    title: "AI inboxes",
    philosophy: "Wake up to an inbox that already triaged itself.",
    examples: ["Triage labels", "Drafted replies", "Follow-up queue"],
  },
  {
    key: "approvals",
    title: "Approval bots",
    philosophy: "Agents propose. Humans decide. Every call is logged.",
    examples: ["Access requests", "Change approvals", "Escalation paths"],
  },
];

function DoodleArrow({ className }: { className?: string }) {
  return (
    <svg
      aria-hidden="true"
      className={className}
      fill="none"
      viewBox="0 0 120 72"
    >
      <path
        className="lp-doodle-arrow-path"
        d="M6 10 C 34 44, 62 58, 104 50 M 88 36 L 106 50 L 90 62"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="5"
      />
    </svg>
  );
}

function Starburst({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 100 100">
      <path
        d="M50 0 L58 30 L84 8 L68 38 L100 34 L72 50 L100 66 L68 62 L84 92 L58 70 L50 100 L42 70 L16 92 L32 62 L0 66 L28 50 L0 34 L32 38 L16 8 L42 30 Z"
        fill="currentColor"
      />
    </svg>
  );
}

function Envelope({ className }: { className?: string }) {
  return (
    <svg aria-hidden="true" className={className} viewBox="0 0 40 28">
      <rect
        fill="currentColor"
        height="26"
        opacity="0.16"
        rx="3"
        width="38"
        x="1"
        y="1"
      />
      <rect
        fill="none"
        height="26"
        rx="3"
        stroke="currentColor"
        strokeWidth="2"
        width="38"
        x="1"
        y="1"
      />
      <path
        d="M2 4 L20 17 L38 4"
        fill="none"
        stroke="currentColor"
        strokeLinecap="round"
        strokeLinejoin="round"
        strokeWidth="2"
      />
    </svg>
  );
}

function SlackMock() {
  return (
    <div className="lp-mock lp-mock-slack">
      <div className="lp-slack-window">
        <div className="lp-slack-side">
          <span className="lp-slack-logo">
            <Image
              alt=""
              height={14}
              src="/landing-page/app-logos/slack.svg"
              width={14}
            />
          </span>
          <i className="is-active"># incidents</i>
          <i># team-ops</i>
          <i># deploys</i>
          <i># product</i>
        </div>
        <div className="lp-slack-main">
          <div className="lp-slack-head"># incidents</div>
          <div className="lp-slack-msg">
            <b>
              <span className="lp-avatar lp-avatar-green" /> Alex
            </b>
            <p>
              Database latency is spiking again
              <span className="lp-flag">!</span>
            </p>
          </div>
          <div className="lp-slack-msg is-agent">
            <b>
              <span className="lp-avatar lp-avatar-lemma">
                <LemmaMark size="xs" />
              </span>
              Lemma Agent <em>APP</em>
            </b>
            <p>
              I&rsquo;ll take it from here.
              <span className="lp-check">
                <svg aria-hidden="true" viewBox="0 0 12 12">
                  <path
                    d="M2 6.5 L5 9 L10 3"
                    fill="none"
                    stroke="currentColor"
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth="2"
                  />
                </svg>
              </span>
            </p>
          </div>
          <div className="lp-slack-msg">
            <b>
              <span className="lp-avatar lp-avatar-purple" /> Ops Team
            </b>
            <p>Handoff complete. Monitoring.</p>
          </div>
        </div>
      </div>
    </div>
  );
}

function TelegramMock() {
  return (
    <div className="lp-mock lp-mock-tg">
      <div className="lp-tg-phone">
        <div className="lp-tg-head">
          <Image
            alt=""
            height={16}
            src="/landing-page/app-logos/telegram.svg"
            width={16}
          />
          Lemma Agent <em>bot</em>
        </div>
        <div className="lp-tg-body">
          <p className="lp-tg-bubble them">Hey! What can I help you with?</p>
          <p className="lp-tg-bubble me">Status of deploy 2024-05-12?</p>
          <div className="lp-tg-card">
            <strong>
              Deploy 2024-05-12 <span className="lp-pill ok">Successful</span>
            </strong>
            <i />
            <i />
            <i className="short" />
            <span className="lp-tg-file">
              <svg aria-hidden="true" viewBox="0 0 14 16">
                <path
                  d="M2 1 h7 l3 3 v11 h-10 z M9 1 v3 h3"
                  fill="none"
                  stroke="currentColor"
                  strokeLinejoin="round"
                  strokeWidth="1.6"
                />
              </svg>
              deploy-2024-05-12.log
              <em>1.2 MB</em>
            </span>
          </div>
        </div>
      </div>
      <div className="lp-tg-miniapp">
        <strong>Lemma Logbook</strong>
        <div className="lp-tg-timeline">
          <span className="done" />
          <span className="done" />
          <span className="now" />
        </div>
        <i />
        <i className="short" />
        <span className="lp-tg-miniapp-cta">Open mini app</span>
      </div>
      <span className="lp-tg-link-note">
        Connected
        <br />
        experience
      </span>
    </div>
  );
}

const TOOL_ROWS = [
  { name: "Onboarding flow", status: "In progress", tone: "purple", owner: "Maya" },
  { name: "API reliability", status: "At risk", tone: "red", owner: "Jordan" },
  { name: "Data pipeline", status: "Healthy", tone: "green", owner: "Li" },
  { name: "Access review", status: "In progress", tone: "purple", owner: "Sam" },
  { name: "Q2 planning", status: "Not started", tone: "grey", owner: "Riley" },
] as const;

function ToolsMock() {
  return (
    <div className="lp-mock lp-mock-tools">
      <div className="lp-tools-table">
        <div className="lp-tools-row is-head">
          <span>Name</span>
          <span>Status</span>
          <span>Owner</span>
        </div>
        {TOOL_ROWS.map((row) => (
          <div className="lp-tools-row" key={row.name}>
            <span>{row.name}</span>
            <span>
              <b className={`lp-pill ${row.tone}`}>{row.status}</b>
            </span>
            <span>{row.owner}</span>
          </div>
        ))}
      </div>
    </div>
  );
}

function CodexMock() {
  return (
    <div className="lp-mock lp-mock-codex">
      <div className="lp-codex-stack" aria-hidden="true">
        <span className="lp-codex-sheet s1" />
        <span className="lp-codex-sheet s2" />
      </div>
      <div className="lp-codex-term">
        <div className="lp-codex-chrome">
          <i />
          <i />
          <i />
        </div>
        <code>
          <span className="lp-codex-prompt">&gt;</span> codex --skin{" "}
          <b>release-operator</b>
          <span className="lp-cursor" />
        </code>
      </div>
      <div className="lp-codex-skin">
        <strong>Release pipeline</strong>
        <div className="lp-step is-done">
          <span className="lp-step-dot" /> 1. Build
        </div>
        <div className="lp-step is-done">
          <span className="lp-step-dot" /> 2. Test
        </div>
        <div className="lp-step is-active">
          <span className="lp-step-dot" /> 3. Deploy to staging
          <em>In progress</em>
          <span className="lp-step-bar">
            <span />
          </span>
        </div>
        <div className="lp-step">
          <span className="lp-step-dot" /> 4. Rollout to prod
          <em className="lp-step-warn">1 approval</em>
        </div>
      </div>
    </div>
  );
}

function PortalMock() {
  return (
    <div className="lp-mock lp-mock-portal">
      <div className="lp-portal-card">
        <header>
          <strong>Acme Corp</strong>
          <b className="lp-pill green">Healthy</b>
        </header>
        <nav>
          <span className="is-active">Overview</span>
          <span>Projects</span>
          <span>Requests</span>
        </nav>
        <div className="lp-portal-health">
          <span>Project health</span>
          <svg aria-hidden="true" viewBox="0 0 120 36">
            <path
              d="M2 28 L16 24 L30 27 L44 18 L58 21 L72 12 L86 15 L100 7 L118 10"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2.5"
            />
          </svg>
        </div>
        <div className="lp-portal-requests">
          <span>Open requests</span>
          <div>
            <i />
            <b className="lp-pill red">High</b>
          </div>
          <div>
            <i />
            <b className="lp-pill yellow">Medium</b>
          </div>
        </div>
      </div>
    </div>
  );
}

function InboxMock() {
  return (
    <div className="lp-mock lp-mock-inbox">
      <div className="lp-inbox-belt" aria-hidden="true">
        <Envelope className="e1" />
        <Envelope className="e2" />
        <Envelope className="e3" />
        <Envelope className="e4" />
      </div>
      <div className="lp-inbox-rows">
        <div>
          <span>Needs reply</span>
          <b className="lp-pill red">12</b>
        </div>
        <div>
          <span>FYI</span>
          <b className="lp-pill purple">8</b>
        </div>
        <div>
          <span>Waiting</span>
          <b className="lp-pill grey">5</b>
        </div>
        <div>
          <span>Resolved</span>
          <b className="lp-pill green">24</b>
        </div>
      </div>
    </div>
  );
}

function ApprovalsMock() {
  return (
    <div className="lp-mock lp-mock-approvals">
      <div className="lp-appr-request">
        <span className="lp-avatar lp-avatar-purple" />
        <div>
          <strong>New access request</strong>
          <i />
          <i className="short" />
        </div>
      </div>
      <div className="lp-appr-node">
        <LemmaMark size="sm" />
      </div>
      <div className="lp-appr-actions">
        <span className="lp-appr-btn ok">
          <svg aria-hidden="true" viewBox="0 0 12 12">
            <path
              d="M2 6.5 L5 9 L10 3"
              fill="none"
              stroke="currentColor"
              strokeLinecap="round"
              strokeLinejoin="round"
              strokeWidth="2"
            />
          </svg>
          Approve
        </span>
        <span className="lp-appr-btn warn">
          <svg aria-hidden="true" viewBox="0 0 12 12">
            <path
              d="M8 2 L10 4 L4 10 L2 10 L2 8 Z"
              fill="none"
              stroke="currentColor"
              strokeLinejoin="round"
              strokeWidth="1.6"
            />
          </svg>
          Revise
        </span>
        <span className="lp-appr-btn err">
          <svg aria-hidden="true" viewBox="0 0 12 12">
            <path
              d="M6 2 L10 6 H7.5 V10 H4.5 V6 H2 Z"
              fill="none"
              stroke="currentColor"
              strokeLinejoin="round"
              strokeWidth="1.4"
            />
          </svg>
          Escalate
        </span>
      </div>
    </div>
  );
}

const TILE_MOCKS: Record<TileKey, () => React.ReactElement> = {
  slack: SlackMock,
  telegram: TelegramMock,
  tools: ToolsMock,
  codex: CodexMock,
  portal: PortalMock,
  inbox: InboxMock,
  approvals: ApprovalsMock,
};

/**
 * Static. The word used to cycle Builds/Runs/Ships/Lives every 2.6s, which put
 * a permanent flicker in the corner of the page while you read the tiles beside
 * it. "Runs" is the one that matters anyway — running is the whole claim.
 */
function BrandTile() {
  return (
    <div className="lp-build-tile lp-tile-brand">
      <Starburst className="lp-burst lp-burst-brand" />
      <LemmaMark className="lp-brand-mark" size="lg" />
      <p className="lp-brand-line">
        <span className="lp-verb-wrap">
          <span className="lp-verb">Runs</span>
        </span>
        <span className="lp-brand-rest">
          on
          <br />
          Lemma.
        </span>
      </p>
      <DoodleArrow className="lp-doodle-arrow lp-arrow-brand" />
      <span className="lp-brand-footer">
        <LemmaMark size="xs" /> Lemma
      </span>
    </div>
  );
}

export function HeroBuildsCollage() {
  const [openTile, setOpenTile] = useState<TileKey | null>(null);

  return (
    <div className="lp-builds">
      <p className="lp-builds-caption">
        <span>What teams build on Lemma</span>
        {/* Was "Hover a tile" — there is no hover on a phone, and the tiles
            have always been click-activated. */}
        <span className="lp-builds-caption-hint">Open a tile for the idea</span>
      </p>
      <div className="lp-builds-grid">
        <BrandTile />
        {BUILD_TILES.map((tile) => {
          const Mock = TILE_MOCKS[tile.key];
          const isOpen = openTile === tile.key;
          return (
            <article
              aria-expanded={isOpen}
              aria-label={`${tile.title}. ${tile.philosophy}`}
              className={`lp-build-tile lp-tile-${tile.key}${
                isOpen ? " is-open" : ""
              }`}
              key={tile.key}
              onClick={() => setOpenTile(isOpen ? null : tile.key)}
              onKeyDown={(event) => {
                if (event.key === "Escape") {
                  setOpenTile(null);
                  return;
                }
                // role="button" promises Enter and Space; only Escape was wired,
                // so the tile was reachable by keyboard but not openable.
                if (event.key === "Enter" || event.key === " ") {
                  event.preventDefault();
                  setOpenTile(isOpen ? null : tile.key);
                }
              }}
              role="button"
              tabIndex={0}
            >
              <Starburst className="lp-burst" />
              <div className="lp-tile-face" aria-hidden={isOpen}>
                <h3 className="lp-tile-title">{tile.title}</h3>
                <Mock />
              </div>
              <div className="lp-tile-overlay">
                <p className="lp-tile-kicker">{tile.title}</p>
                <p className="lp-tile-philosophy">{tile.philosophy}</p>
                <ul className="lp-tile-examples">
                  {tile.examples.map((example) => (
                    <li key={example}>{example}</li>
                  ))}
                </ul>
                <Link
                  className="lp-tile-cta"
                  href="/templates"
                  onClick={(event) => event.stopPropagation()}
                >
                  See it in templates
                  <ArrowRight aria-hidden size={12} />
                </Link>
              </div>
            </article>
          );
        })}
      </div>
    </div>
  );
}
