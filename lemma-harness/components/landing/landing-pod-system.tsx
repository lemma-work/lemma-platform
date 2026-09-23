"use client";

import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import type { LemmaIcon } from "@/components/ui/icons";
import {
  AppWindow,
  Code,
  Table,
  GitBranch,
  ShieldCheck,
} from "@/components/ui/icons";

/**
 * §4 — the pod, explained from the ground up.
 *
 * The stack is built in the order the system is actually built: data and files
 * first, then the deterministic functions over them, then the agents and
 * workflows that move work, then the apps and surfaces people meet — all
 * sitting on a permissions plate that decides who and what may touch any of it.
 *
 * The stack sticks while detail cards scroll past it, so each layer gets real
 * substance (a schema, a signature, an agent's scope, a workflow graph) rather
 * than a one-line gloss. Clicking a plane scrolls its card into view.
 *
 * As you scroll, the active plane counter-rotates out of the isometric frame to
 * stand up and face the viewer, then lays back into the stack when the next one
 * takes over — so the layer you are reading about is literally presenting
 * itself.
 */

const LAYERS = [
  { key: "data", title: "Tables and files", question: "What it remembers" },
  { key: "functions", title: "Functions", question: "What it does the same way every time" },
  { key: "agents", title: "Agents and workflows", question: "What moves the work" },
  { key: "apps", title: "Apps and surfaces", question: "Where people meet it" },
  { key: "base", title: "Permissions and connectors", question: "The boundary it all sits on" },
] as const;

type LayerKey = (typeof LAYERS)[number]["key"];

/** A large, faint glyph so a plane reads as its layer at a glance. */
const PLANE_MARK: Record<LayerKey, LemmaIcon> = {
  data: Table,
  functions: Code,
  agents: GitBranch,
  apps: AppWindow,
  base: ShieldCheck,
};

/* ── Detail cards: the actual depth ──────────────────────────────────── */

function DataDetail() {
  return (
    <div className="lp-pd-body">
      <div className="lp-pd-panel">
        <header>
          <strong>tickets</strong>
          <span className="lp-pd-badge">1,284 rows</span>
        </header>
        <table className="lp-pd-schema">
          <tbody>
            {[
              ["id", "uuid", "primary key"],
              ["customer", "relation", "→ customers"],
              ["amount", "money", "indexed"],
              ["status", "enum", "open · drafted · closed"],
              ["created_at", "timestamp", "auto"],
            ].map(([col, type, note]) => (
              <tr key={col}>
                <td>{col}</td>
                <td>
                  <em>{type}</em>
                </td>
                <td>{note}</td>
              </tr>
            ))}
          </tbody>
        </table>
        <footer className="lp-pd-rls">
          <i />
          Row-level security: the classifier agent sees open tickets only.
        </footer>
      </div>

      <div className="lp-pd-panel">
        <header>
          <strong>refund-policy.md</strong>
          <span className="lp-pd-badge">markdown memory</span>
        </header>
        <div className="lp-pd-md">
          <p>## Thresholds</p>
          <p>Refunds above $250 require an approver.</p>
          <p>Repeat requests inside 30 days escalate to a lead.</p>
        </div>
        <footer className="lp-pd-note">
          Full-text searchable, permission-scoped, and read by agents alongside
          the tables. This is where policy lives: versioned, editable, and read
          at run time.
        </footer>
      </div>
    </div>
  );
}

function FunctionsDetail() {
  return (
    <div className="lp-pd-body">
      <div className="lp-pd-panel is-code">
        <header>
          <strong>check_policy</strong>
          <span className="lp-pd-badge is-blue">deterministic</span>
        </header>
        <pre>
          <code>
            <span>
              <b>fn</b> check_policy(amount, customer) {"{"}
            </span>
            <span> policy = read(&quot;refund-policy.md&quot;)</span>
            <span>
              {" "}
              <b>if</b> amount &gt; policy.threshold
            </span>
            <span> return {"{ requiresApproval: true }"}</span>
            <span>{"}"}</span>
          </code>
        </pre>
      </div>

      <div className="lp-pd-panel">
        <header>
          <strong>The predictable half</strong>
        </header>
        <ul className="lp-pd-list">
          <li>
            <i className="is-blue" />
            Same input, same output, every time. Plain code, end to end.
          </li>
          <li>
            <i className="is-blue" />
            Validators, state transitions, and outbound actions live here.
          </li>
          <li>
            <i className="is-blue" />
            Agents call them as tools, so judgment and rules stay separable.
          </li>
        </ul>
      </div>
    </div>
  );
}

function AgentsDetail() {
  return (
    <div className="lp-pd-body">
      <div className="lp-pd-panel">
        <header>
          <strong>Classifier</strong>
          <span className="lp-pd-badge is-purple">agent</span>
        </header>
        <div className="lp-pd-scope">
          <p>
            <span>Can read</span>
            <em>tickets · customers · refund-policy.md</em>
          </p>
          <p>
            <span>Can write</span>
            <em>tickets.status</em>
          </p>
          <p className="is-deny">
            <span>Cannot touch</span>
            <em>refunds · api_keys · connectors</em>
          </p>
          <p>
            <span>Tools</span>
            <em>check_policy · search · table.update</em>
          </p>
        </div>
      </div>

      <div className="lp-pd-panel">
        <header>
          <strong>refund-review</strong>
          <span className="lp-pd-badge is-blue">workflow</span>
        </header>
        <div className="lp-pd-flow">
          {[
            ["classify", "agent", "done"],
            ["check_policy", "function", "done"],
            ["approve", "human", "wait"],
            ["send", "function", "idle"],
          ].map(([step, kind, state]) => (
            <div className={`lp-pd-node is-${state}`} key={step}>
              <i />
              <strong>{step}</strong>
              <small>{kind}</small>
            </div>
          ))}
        </div>
        <footer className="lp-pd-note">
          It pauses at <b>approve</b>{" "}
          and resumes on a person&apos;s decision, days later if that is how
          long it takes. Triggered by a table event, schedule, webhook, message,
          or the API.
        </footer>
      </div>
    </div>
  );
}

function AppsDetail() {
  return (
    <div className="lp-pd-body">
      <div className="lp-pd-panel">
        <header>
          <strong>Support Ops</strong>
          <span className="lp-pd-badge is-green">deployed</span>
        </header>
        <div className="lp-pd-app">
          <div className="lp-pd-app-bar">
            <span />
            <span />
            <em>support-ops.lemma.work</em>
          </div>
          <div className="lp-pd-app-rows">
            <p>
              <b>tkt_418</b> Northwind · $420
              <span className="lp-pd-chip is-amber">needs approval</span>
            </p>
            <p>
              <b>tkt_417</b> Halden · $38
              <span className="lp-pd-chip is-green">sent</span>
            </p>
            <p>
              <b>tkt_416</b> Ravel · $120
              <span className="lp-pd-chip is-green">sent</span>
            </p>
          </div>
        </div>
        <footer className="lp-pd-note">
          Built on the same APIs the agents use. An agent writes, and the app
          is already showing it.
        </footer>
      </div>

      <div className="lp-pd-panel">
        <header>
          <strong>The same pod, other surfaces</strong>
        </header>
        <div className="lp-pd-surfaces">
          {[
            ["Slack", "/landing-page/app-logos/slack.svg"],
            ["ChatGPT", "/landing-page/app-logos/chatgpt.svg"],
            ["Claude", "/landing-page/app-logos/claude.svg"],
            ["Telegram", "/landing-page/app-logos/telegram.svg"],
            ["Gmail", "/landing-page/app-logos/gmail.svg"],
            ["API", "/landing-page/app-logos/api.svg"],
          ].map(([label, src]) => (
            <span key={label}>
              <Image src={src} alt="" width={16} height={16} />
              {label}
            </span>
          ))}
        </div>
        <footer className="lp-pd-note">
          Each one resolves who is asking and what they may ask for. The pod
          holds the data.
        </footer>
      </div>
    </div>
  );
}

function BaseDetail() {
  return (
    <div className="lp-pd-body">
      <div className="lp-pd-panel">
        <header>
          <strong>Access</strong>
          <span className="lp-pd-badge is-yellow">people and agents alike</span>
        </header>
        <div className="lp-pd-matrix">
          <div className="lp-pd-matrix-head">
            <span />
            <span>tickets</span>
            <span>refunds</span>
            <span>connectors</span>
          </div>
          {[
            ["Dana", "Owner", "edit", "approve", "manage"],
            ["Maya", "Member", "edit", "request", "none"],
            ["Classifier", "Agent", "edit", "none", "none"],
          ].map(([who, kind, a, b, c]) => (
            <div className="lp-pd-matrix-row" key={who}>
              <span>
                <strong>{who}</strong>
                <small>{kind}</small>
              </span>
              {[a, b, c].map((value, index) => (
                <span key={`${who}-${index}`}>
                  <em className={`lp-pd-grant is-${value}`}>{value}</em>
                </span>
              ))}
            </div>
          ))}
        </div>
      </div>

      <div className="lp-pd-panel">
        <header>
          <strong>Connectors</strong>
          <span className="lp-pd-badge">what the pod can reach</span>
        </header>
        <div className="lp-pd-conns">
          <p>
            <strong>Stripe</strong>
            <small>billing@northstar.co</small>
            <em>issue refunds</em>
          </p>
          <p>
            <strong>Gmail</strong>
            <small>support@northstar.co</small>
            <em>read threads · create drafts</em>
          </p>
        </div>
        <footer className="lp-pd-note">
          Every connection shows the account, its access, and what uses it, so
          &quot;the agent can email customers&quot; is a setting you can see.
        </footer>
      </div>
    </div>
  );
}

const DETAILS: Record<LayerKey, () => React.JSX.Element> = {
  data: DataDetail,
  functions: FunctionsDetail,
  agents: AgentsDetail,
  apps: AppsDetail,
  base: BaseDetail,
};

export function PodSystemSection() {
  const [active, setActive] = useState<LayerKey>("data");
  const [assembled, setAssembled] = useState(false);
  const sceneRef = useRef<HTMLDivElement>(null);
  const cardsRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const node = sceneRef.current;
    if (!node) return;
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            setAssembled(true);
            observer.disconnect();
          }
        }
      },
      { threshold: 0.2 },
    );
    observer.observe(node);
    return () => observer.disconnect();
  }, []);

  // Nearest-card-to-centre beats a narrow IntersectionObserver band here: the
  // band flipped on tiny scrolls, which read as twitchy. A hysteresis margin
  // means the active layer only changes once another card is clearly closer.
  useEffect(() => {
    const list = cardsRef.current;
    if (!list) return;
    const cards = Array.from(list.querySelectorAll<HTMLElement>("[data-layer]"));
    if (cards.length === 0) return;

    let frame = 0;
    let current: LayerKey | null = null;

    const pick = () => {
      frame = 0;
      const centre = window.innerHeight / 2;
      let best: LayerKey | null = null;
      let bestDistance = Number.POSITIVE_INFINITY;
      let currentDistance = Number.POSITIVE_INFINITY;

      for (const card of cards) {
        const box = card.getBoundingClientRect();
        const distance = Math.abs(box.top + box.height / 2 - centre);
        const key = card.dataset.layer as LayerKey;
        if (distance < bestDistance) {
          bestDistance = distance;
          best = key;
        }
        if (key === current) currentDistance = distance;
      }

      // 80px of hysteresis: a new card has to win by a clear margin.
      if (best && best !== current && bestDistance < currentDistance - 80) {
        current = best;
        setActive(best);
      } else if (best && current === null) {
        current = best;
        setActive(best);
      }
    };

    const onScroll = () => {
      if (frame) return;
      frame = window.requestAnimationFrame(pick);
    };

    pick();
    window.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", onScroll);
    return () => {
      if (frame) window.cancelAnimationFrame(frame);
      window.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", onScroll);
    };
  }, []);

  const goTo = (key: LayerKey) => {
    const target = cardsRef.current?.querySelector<HTMLElement>(
      `[data-layer="${key}"]`,
    );
    target?.scrollIntoView({ behavior: "smooth", block: "center" });
  };

  return (
    <section className="lp-section lp-podsys-section" id="inside">
      <div className="lp-section-inner">
        <div className="lp-podsys-head lp-reveal">
          <p className="lp-section-kicker">Everything lives in a pod</p>
          <h2 className="lp-section-title">
            Built from the ground up. <span>One boundary around all of it.</span>
          </h2>
          <p className="lp-section-subhead">
            Records first, then the functions over them, then the agents and
            workflows that move work, then the app people open. Permissions hold
            all of it.
          </p>
        </div>

        <div className="lp-podsys-layout">
          <div className="lp-podsys-col">
            <div className="lp-podsys-sticky">
            <div
              className={`lp-iso${assembled ? " is-assembled" : ""} is-focused`}
              ref={sceneRef}
            >
              <div className="lp-iso-scene" aria-hidden="true">
                {LAYERS.map((layer) => (
                  <div
                    className={`lp-iso-plane is-${layer.key}${
                      active === layer.key ? " is-active" : ""
                    }`}
                    key={layer.key}
                  >
                    <span className="lp-iso-plane-face" />
                    <span className="lp-iso-mark">
                      {(() => {
                        const Mark = PLANE_MARK[layer.key];
                        return <Mark weight="thin" />;
                      })()}
                    </span>
                  </div>
                ))}
              </div>

              {/* Callouts, not overlays. Inside the 3D scene a label was either
                  squashed to ~5px by the isometric frame, or — counter-rotated
                  upright — sliced where the standing card intersected its own
                  plane. Sitting them off the left vertex on a leader line keeps
                  the type crisp and leaves the planes themselves visible. */}
              <div className="lp-iso-labels">
                {LAYERS.map((layer) => (
                  <button
                    className={`lp-iso-label is-${layer.key}${
                      active === layer.key ? " is-active" : ""
                    }`}
                    key={layer.key}
                    onClick={() => goTo(layer.key)}
                    type="button"
                  >
                    <span className="lp-iso-name">{layer.title}</span>
                    <span className="lp-iso-hint">{layer.question}</span>
                  </button>
                ))}
              </div>
            </div>

            </div>
          </div>

          <div className="lp-podsys-cards" ref={cardsRef}>
            {LAYERS.map((layer) => {
              const Detail = DETAILS[layer.key];
              const Mark = PLANE_MARK[layer.key];
              return (
                <article
                  className={`lp-pd-card is-${layer.key}${
                    active === layer.key ? " is-active" : ""
                  }`}
                  data-layer={layer.key}
                  key={layer.key}
                >
                  {/* Icon, not a numeral: §3 already owns 01–05 for the journey,
                      and a second 01–05 here made the same badge mean two
                      different things one section apart. */}
                  <header className="lp-pd-head">
                    <span className="lp-pd-glyph" aria-hidden="true">
                      <Mark weight="bold" />
                    </span>
                    <div>
                      <h3>{layer.title}</h3>
                      <p>{layer.question}</p>
                    </div>
                  </header>
                  <Detail />
                </article>
              );
            })}
          </div>
        </div>
      </div>
    </section>
  );
}
