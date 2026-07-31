"use client";

import Image from "next/image";
import Link from "next/link";
import { useState } from "react";
import { ArrowRight, Check, Copy } from "@/components/ui/icons";
import {
  PUBLIC_TEMPLATES,
  templateCoverPath,
  templateRunHref,
} from "@/lib/templates/catalog";

/**
 * §2 — the bottleneck moved.
 *
 * Framed on YC's "A Cloud for Small Software": easy to build now, still hard to
 * deploy and share. But "shared" read as pure hosting, which undersells the
 * product — for agent-built software, shared has to mean *running*: the team is
 * in, the agents keep going, and it reaches people where they already work.
 * The turn names all three, which also sets up §3, §4 and §5.
 */

const GAP_BEATS = [
  "Your agent writes it in an afternoon.",
  "Then it sits on your laptop.",
  "Getting it to nine people is the whole project.",
] as const;

const SHARED_MEANS = [
  {
    lead: "Your team is in.",
    rest: "A link, and the access you chose — for people and for agents.",
  },
  {
    lead: "It keeps working.",
    rest: "Agents run on schedules and events, and stop where you want a human.",
  },
  {
    lead: "It reaches them.",
    rest: "Slack, ChatGPT, Claude, Telegram, email — or the app itself.",
  },
] as const;

export function GapSection() {
  return (
    <section className="lp-section lp-gap-section" id="gap">
      <div className="lp-section-inner">
        <div className="lp-reveal">
          <p className="lp-section-kicker">The bottleneck moved</p>
          <h2 className="lp-section-title">
            Building it got easy. <span>Sharing it didn&apos;t.</span>
          </h2>
          <p className="lp-section-subhead">
            A tool for nine people should be as easy to hand over as a doc — and
            it should still be working after you hand it over.
          </p>
        </div>

        <ol className="lp-gap-beats">
          {GAP_BEATS.map((line) => (
            <li className="lp-reveal" key={line}>
              <span className="lp-gap-rail" />
              <p className="lp-gap-beat">{line}</p>
            </li>
          ))}
        </ol>

        {/* The turn is the page's thesis, so it gets statement weight and its
            own band rather than being the fourth item in a list. */}
        <div className="lp-gap-turn lp-reveal">
          <p className="lp-gap-turn-line">
            On Lemma, <span>shared means running.</span>
          </p>
          <ul className="lp-gap-means">
            {SHARED_MEANS.map((item) => (
              <li key={item.lead}>
                <b>{item.lead}</b>
                <span>{item.rest}</span>
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}

/* ── §6 Build ─────────────────────────────────────────────────────────────
 * Real agent terminal chrome rather than a generic typing animation: the
 * claim is "the agent you already use builds the whole system", so the proof
 * should look like the tool the reader actually has open.
 */

function ClaudeCodeTerminal() {
  return (
    <div className="lp-term lp-term-claude" aria-hidden="true">
      <header className="lp-term-bar">
        <span className="lp-term-dots">
          <i />
          <i />
          <i />
        </span>
        <span className="lp-term-title">
          <em className="lp-term-star">✻</em> Claude Code
        </span>
        <span className="lp-term-cwd">~/support-desk</span>
      </header>

      <div className="lp-term-body">
        <p className="lp-term-user">
          <b>&gt;</b> Build a support desk pod: tickets, a refund policy, a
          classifier agent, and an approval before anything over $250 goes out.
        </p>

        <p className="lp-term-think">
          <em className="lp-term-star">✻</em> Designing the resource model…
          <small>12s · 3.1k tokens</small>
        </p>

        <div className="lp-term-tool">
          <span className="lp-term-tool-name">Write</span>
          <span className="lp-term-tool-arg">tables/tickets.yaml</span>
          <span className="lp-term-tool-diff">
            <b>+34</b>
          </span>
        </div>
        <div className="lp-term-tool">
          <span className="lp-term-tool-name">Write</span>
          <span className="lp-term-tool-arg">agents/classifier.yaml</span>
          <span className="lp-term-tool-diff">
            <b>+21</b>
          </span>
        </div>
        <div className="lp-term-tool">
          <span className="lp-term-tool-name">Write</span>
          <span className="lp-term-tool-arg">workflows/refund-review.yaml</span>
          <span className="lp-term-tool-diff">
            <b>+48</b> <i>−2</i>
          </span>
        </div>
        <div className="lp-term-tool">
          <span className="lp-term-tool-name">Bash</span>
          <span className="lp-term-tool-arg">lemma pod import .</span>
          <span className="lp-term-tool-ok">✓</span>
        </div>

        <ul className="lp-term-todo">
          <li className="is-done">
            <i>✓</i> Model tickets, customers, refunds
          </li>
          <li className="is-done">
            <i>✓</i> Scope the classifier to tickets only
          </li>
          <li className="is-done">
            <i>✓</i> Gate refunds over $250 behind an approver
          </li>
          <li className="is-active">
            <i>◐</i> Verify the workflow pauses correctly
          </li>
        </ul>

        <p className="lp-term-out">
          Ran <b>refund-review</b> with a $420 test ticket — paused at{" "}
          <b>approve</b>, as expected.
        </p>
      </div>

      <footer className="lp-term-foot">
        <span className="lp-term-chip">Opus 5</span>
        <span className="lp-term-chip">effort: high</span>
        <span className="lp-term-chip is-accept">auto-accept edits on</span>
        <span className="lp-term-hint">shift+tab to cycle</span>
      </footer>
    </div>
  );
}

function CodexTerminal() {
  return (
    <div className="lp-term lp-term-codex" aria-hidden="true">
      <header className="lp-term-bar">
        <span className="lp-term-dots">
          <i />
          <i />
          <i />
        </span>
        <span className="lp-term-title">
          <em className="lp-term-mark">◇</em> Codex
        </span>
        <span className="lp-term-cwd">~/support-desk</span>
      </header>

      <div className="lp-term-body">
        <p className="lp-term-user">
          <b>›</b> Add a policy check before refunds and expose it in the app.
        </p>

        <p className="lp-term-think">
          Thought for 0.6s
          <small>reading pod resources</small>
        </p>

        <div className="lp-term-steps">
          <p className="is-done">
            <i>✓</i> Read <b>files/refund-policy.md</b>
          </p>
          <p className="is-done">
            <i>✓</i> Add <b>functions/check-policy.ts</b>
          </p>
          <p className="is-active">
            <i>▸</i> Wire it into <b>refund-review</b>
          </p>
          <p className="is-idle">
            <i>○</i> Redeploy <b>support-ops</b>
          </p>
        </div>

        <div className="lp-term-progress">
          <span className="is-62" />
        </div>
        <p className="lp-term-count">2/4 steps complete</p>

        <div className="lp-term-diff">
          <p className="lp-term-diff-head">functions/check-policy.ts</p>
          <p className="is-add">
            <span>+</span> if (amount &gt; policy.threshold) &#123;
          </p>
          <p className="is-add">
            <span>+</span> return &#123; requiresApproval: true &#125;
          </p>
          <p className="is-add">
            <span>+</span> &#125;
          </p>
        </div>
      </div>

      <footer className="lp-term-foot">
        <span className="lp-term-chip">gpt-5-codex</span>
        <span className="lp-term-chip">workspace-write</span>
        <span className="lp-term-hint">running in ~/support-desk</span>
      </footer>
    </div>
  );
}

const buildPrompts = {
  "claude-code": `Use the Lemma builder skills available in this workspace to build a complete app for [describe the job].

Start from the person doing the work and the outcome they need. Then design the smallest useful operating loop.

Build the whole system on Lemma:
- the app people open and use
- the tables and docs that hold shared state
- the functions and workflows that move the work
- the agents that can help
- clear permissions and human review points

Use the existing repository and Lemma tooling. Keep the interface calm, specific to the job, and ready for a real team to use. Before changing anything, show me the proposed workflow and resource model.`,
  codex: `Use the installed Lemma builder skill to build a complete app for [describe the job].

Begin with the user, the job they are trying to finish, and the durable state the work needs. Propose the smallest complete operating loop before implementation.

The finished Lemma pod should include:
- a focused app for the daily work
- shared tables and docs
- the required functions and workflows
- agents with scoped access
- explicit human decisions where they matter

Work from the current repository, reuse its conventions, and verify the real app in the browser. The result should feel like finished software for this job, not a generic dashboard.`,
} as const;

type PromptTarget = keyof typeof buildPrompts;

/** Claude Code and Codex are the tabs; this rail lists only what else works. */
const ALSO_WORKS_WITH = [
  { label: "Cursor", src: "/harnesslogos/cursor.png" },
  { label: "OpenCode", src: "/harnesslogos/opencode.png" },
  { label: "Antigravity", src: "/harnesslogos/antigravity.png" },
] as const;

export function BuildSection() {
  const [tab, setTab] = useState<PromptTarget>("claude-code");
  const [copied, setCopied] = useState<PromptTarget | null>(null);

  const copyPrompt = async (target: PromptTarget) => {
    await navigator.clipboard.writeText(buildPrompts[target]);
    setCopied(target);
    window.setTimeout(() => {
      setCopied((current) => (current === target ? null : current));
    }, 1800);
  };

  return (
    <section className="lp-section lp-build-section" id="build">
      <div className="lp-section-inner lp-reveal">
        <p className="lp-section-kicker">Build it</p>
        <h2 className="lp-section-title">
          The agent you already use builds{" "}
          <span>the whole system.</span>
        </h2>
        <p className="lp-section-subhead">
          Not just the frontend. Tables, agents, workflows and permissions,
          written as files and checked by the same agent that wrote them.
        </p>

        <div className="lp-build-grid">
          <div className="lp-build-shell">
          <div className="lp-build-tabs" role="tablist" aria-label="Coding agents">
            <button
              aria-selected={tab === "claude-code"}
              className={tab === "claude-code" ? "is-active" : ""}
              onClick={() => setTab("claude-code")}
              role="tab"
              type="button"
            >
              <Image
                src="/harnesslogos/claudecode.png"
                alt=""
                width={16}
                height={16}
                unoptimized
              />
              Claude Code
            </button>
            <button
              aria-selected={tab === "codex"}
              className={tab === "codex" ? "is-active" : ""}
              onClick={() => setTab("codex")}
              role="tab"
              type="button"
            >
              <Image
                src="/harnesslogos/codex.png"
                alt=""
                width={16}
                height={16}
                unoptimized
              />
              Codex
            </button>

          </div>

          {tab === "claude-code" ? <ClaudeCodeTerminal /> : <CodexTerminal />}
          </div>

          <aside className="lp-build-side">
            <p className="lp-build-side-label">Also works with</p>
          <ul className="lp-build-harnesses">
            {ALSO_WORKS_WITH.map((harness) => (
              <li key={harness.label}>
                <Image
                  src={harness.src}
                  alt=""
                  width={20}
                  height={20}
                  unoptimized
                />
                <span>{harness.label}</span>
              </li>
            ))}
          </ul>

            <div className="lp-build-prompts">
              <button
                className="lp-build-prompt-button"
                onClick={() => copyPrompt(tab)}
                type="button"
              >
                {copied === tab ? <Check aria-hidden /> : <Copy aria-hidden />}
                <span>
                  {copied === tab
                    ? "Copied"
                    : `Copy the ${tab === "codex" ? "Codex" : "Claude Code"} prompt`}
                </span>
              </button>
              <span aria-live="polite" className="lp-build-copy-status">
                {copied ? "Build prompt copied." : ""}
              </span>
            </div>

            <p className="lp-build-side-foot">
              Point it at an empty directory and describe the job. It writes the
              tables, agents, workflows and permissions as files, imports them,
              then runs the workflow to check it pauses where it should.
            </p>
          </aside>
        </div>
      </div>
    </section>
  );
}

/* ── §7 Examples ──────────────────────────────────────────────────────────
 * The hero eyebrow claims "open source" and the subhead claims "run anywhere";
 * the closer promises you can share it before you log off. Nothing between them
 * showed why either is true. The portable band is that proof, and it is also
 * where "Install and remix" stops being a button label and becomes a loop.
 */

const showcaseTemplates = PUBLIC_TEMPLATES.slice(0, 8);

const PORTABLE_CLAIMS = [
  {
    lead: "Your machine.",
    rest: "The whole stack runs on your laptop.",
  },
  {
    lead: "Your cloud.",
    rest: "Self-host it, or use Lemma Cloud.",
  },
  {
    lead: "Your models.",
    rest: "Your Claude or Codex subscription, Lemma-managed models, or any compatible endpoint.",
  },
  {
    lead: "Your code.",
    rest: "AGPLv3 core. Apache-2.0 SDKs, CLI, and skills.",
  },
] as const;

function PortableBand() {
  return (
    <div className="lp-portable lp-reveal">
      <div className="lp-portable-loop">
        <p className="lp-portable-lead">
          A pod is files. <span>Export it, share it, remix it.</span>
        </p>
        <pre className="lp-portable-code" aria-label="Export and import a pod">
          <code>
            <span>
              <b>$</b> lemma pod export ./support-desk
              <em># the whole system, as files</em>
            </span>
            <span>
              <b>$</b> lemma pod import ./support-desk
              <em># ship it back, or anywhere else</em>
            </span>
          </code>
        </pre>
      </div>

      <ul className="lp-portable-claims">
        {PORTABLE_CLAIMS.map((claim) => (
          <li key={claim.lead}>
            <b>{claim.lead}</b>
            <span>{claim.rest}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

export function ExamplesSection() {
  return (
    <section className="lp-section lp-examples-section" id="examples">
      <div className="lp-section-inner lp-reveal">
        <p className="lp-section-kicker">Examples</p>
        <h2 className="lp-section-title">
          Complete pods, running. <span>Install one and make it yours.</span>
        </h2>
        <p className="lp-section-subhead">
          Whole working systems, not starters — and every one of them open
          source. Open it, see how it was built, make it yours.
        </p>

        <div className="lp-examples-grid">
          {showcaseTemplates.map((template) => (
            <Link
              className="lp-template-card"
              href={templateRunHref(template)}
              key={template.slug}
            >
              <span className="lp-template-art">
                <Image
                  alt=""
                  fill
                  sizes="(max-width: 700px) 100vw, (max-width: 1100px) 50vw, 25vw"
                  src={templateCoverPath(template)}
                  unoptimized
                />
              </span>
              <span className="lp-template-card-copy">
                {/* Category only. "Open source pod" was on all eight cards, and
                    on the two long categories it wrapped to a second line and
                    knocked that card's title out of alignment with its row. */}
                <span className="lp-template-meta">
                  <span>{template.category}</span>
                </span>
                <strong>{template.name}</strong>
                <span className="lp-template-description">
                  {template.description}
                </span>
                <span className="lp-template-open">
                  Install and remix
                  <ArrowRight aria-hidden />
                </span>
              </span>
            </Link>
          ))}
        </div>

        <div className="lp-examples-more">
          <Link className="lp-button secondary" href="/templates">
            Browse all templates
            <ArrowRight aria-hidden />
          </Link>
        </div>

        <PortableBand />
      </div>
    </section>
  );
}
