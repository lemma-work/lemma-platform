"use client";

import Image from "next/image";
import Link from "next/link";
import { GithubLogo, Minus, Plus } from "@/components/ui/icons";
import { useEffect, useRef, useState } from "react";
import { Logo } from "@/components/brand/logo";
import type { SurfaceMode } from "./landing-data";
import {
  githubUrl,
  podBlocks,
  showcaseCards,
  surfaceModes,
} from "./landing-data";
import {
  HeroTicker,
  MachineAtWork,
  PodBlockGlyph,
  TypingTerminal,
} from "./landing-animations";
import { StackComparison, SurfacePreview } from "./landing-surfaces";

export default function LandingPage() {
  const [activeSurface, setActiveSurface] =
    useState<SurfaceMode["key"]>("slack");
  const [flippedCards, setFlippedCards] = useState<Set<string>>(
    () => new Set(),
  );
  const [stackCompare, setStackCompare] = useState(50);
  const [openPodBlock, setOpenPodBlock] = useState<string>("data");
  const podIframeRef = useRef<HTMLIFrameElement>(null);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const root = rootRef.current;
    if (!root) return;

    root.classList.add("lp-js");
    const observer = new IntersectionObserver(
      (entries) => {
        for (const entry of entries) {
          if (entry.isIntersecting) {
            entry.target.classList.add("is-inview");
            observer.unobserve(entry.target);
          }
        }
      },
      { threshold: 0.12, rootMargin: "0px 0px -8% 0px" },
    );
    for (const el of root.querySelectorAll(".lp-reveal")) observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const openPodSection = (key: string) => {
    setOpenPodBlock(key);
    podIframeRef.current?.contentWindow?.postMessage(
      { type: "pod-drill", section: key },
      "*",
    );
  };

  const currentSurface =
    surfaceModes.find((surface) => surface.key === activeSurface) ??
    surfaceModes[0];

  return (
    <div className="lp-react" ref={rootRef}>
      <header className="lp-header" aria-label="Site header">
        <Link className="lp-brand" href="/">
          <Logo
            className="lp-brand-logo"
            size="sm"
            variant="mark-wordmark"
          />
        </Link>
        <nav className="lp-nav" aria-label="Primary navigation">
          <a href="#inside">Product</a>
          <a href="/docs" target="_blank" rel="noreferrer">
            Docs
          </a>
          <a href="#showcase">Showcase</a>
          <a
            className="lp-gh-link"
            href={githubUrl}
            target="_blank"
            rel="noreferrer"
          >
            <GithubLogo aria-hidden className="lp-gh-icon" />
            GitHub
          </a>
        </nav>
        <div className="lp-header-actions">
          <Link href="/auth">Log in</Link>
          <a
            className="lp-button primary"
            href={githubUrl}
            target="_blank"
            rel="noreferrer"
          >
            <GithubLogo aria-hidden className="lp-gh-icon" />
            Star on GitHub
          </a>
        </div>
      </header>

      <main>
        <section className="lp-hero" aria-labelledby="hero-title">
          <div className="lp-hero-copy">
            <p className="lp-eyebrow">
              <span className="lp-eyebrow-badge">
                <GithubLogo aria-hidden className="lp-gh-icon" />
                Open source
              </span>
              AI-native workspace
            </p>
            <h1 className="lp-hero-headline" id="hero-title">
              <span className="lp-hl-line">How humans and AI do</span>
              <span className="lp-hl-line">
                <span className="lp-hl-accent">real work</span>, together.
              </span>
            </h1>
            <p className="lp-subhead">
              Teammates work from apps, Slack, WhatsApp, and email. Agents work
              from the CLI. Lemma holds the shared state that keeps it all
              moving.
            </p>

            <div className="lp-actions">
              <Link className="lp-button primary" href="/auth">
                Get started
              </Link>
              <a
                className="lp-button secondary"
                href={githubUrl}
                target="_blank"
                rel="noreferrer"
              >
                <GithubLogo aria-hidden className="lp-gh-icon" />
                View on GitHub
              </a>
            </div>

            <HeroTicker />
          </div>

          <section className="lp-hero-theater" aria-label="Inside a Lemma pod">
            <div className="lp-theater-stage">
              <div className="lp-product-frame">
                <iframe
                  title="Inside a Lemma pod"
                  src="/landing-page/pod-workspace.html?embed=1&section=agents"
                />
              </div>
            </div>
          </section>
        </section>

        <section className="lp-section lp-pod-section" id="inside">
          <div className="lp-section-inner lp-reveal">
            <p className="lp-section-kicker">Inside a pod</p>
            <h2 className="lp-section-title">
              A pod is where AI and humans <span>work together.</span>
            </h2>
            <p className="lp-section-subhead">
              Tables, agents, workflows, and approval gates - six building
              blocks, one coherent system.
            </p>

            <div className="lp-pod-explorer">
              <div className="lp-pod-acc" aria-label="Pod building blocks">
                {podBlocks.map((block) => {
                  const isOpen = openPodBlock === block.key;
                  return (
                    <div
                      className={`lp-pod-acc-item${isOpen ? " is-open" : ""}`}
                      key={block.key}
                    >
                      <button
                        type="button"
                        className="lp-pod-acc-head"
                        aria-expanded={isOpen}
                        onClick={() => openPodSection(block.key)}
                      >
                        <span className="lp-pod-acc-glyph" aria-hidden="true">
                          <PodBlockGlyph kind={block.key} />
                        </span>
                        <span className="lp-pod-acc-titles">
                          <strong>{block.title}</strong>
                          <span>{block.summary}</span>
                        </span>
                        <span className="lp-pod-acc-toggle" aria-hidden="true">
                          {isOpen ? <Minus aria-hidden="true" /> : <Plus aria-hidden="true" />}
                        </span>
                      </button>
                      <div className="lp-pod-acc-detail">
                        <div>
                          <p>{block.detail}</p>
                        </div>
                      </div>
                    </div>
                  );
                })}
              </div>

              <aside className="lp-pod-visual" aria-label="Inside a pod interactive component">
                <iframe
                  ref={podIframeRef}
                  title="Inside a pod"
                  src="/landing-page/in-the-pod-component.html"
                />
              </aside>
            </div>
          </div>
        </section>

        <section
          className="lp-section lp-surfaces-section"
          id="surfaces"
          aria-labelledby="surfaces-title"
        >
          <div className="lp-section-inner lp-surfaces-grid lp-reveal">
            <div className="lp-surfaces-copy">
              <p className="lp-section-kicker">Where it surfaces</p>
              <h2 className="lp-section-title" id="surfaces-title">
                {currentSurface.headline.split(" ").slice(0, 2).join(" ")}{" "}
                <span>
                  {currentSurface.headline.split(" ").slice(2).join(" ")}
                </span>
              </h2>
              <p className="lp-section-subhead">{currentSurface.body}</p>

              <div
                className="lp-surface-picker"
                role="tablist"
                aria-label="Surface examples"
              >
                {surfaceModes.map((surface) => (
                  <button
                    aria-selected={activeSurface === surface.key}
                    className={activeSurface === surface.key ? "is-active" : ""}
                    key={surface.key}
                    onClick={() => setActiveSurface(surface.key)}
                    role="tab"
                    type="button"
                  >
                    <span
                      className="lp-surface-picker-icons"
                      aria-hidden="true"
                    >
                      {surface.logos.map((logo) => (
                        <Image
                          key={logo.label}
                          src={logo.src}
                          alt=""
                          width={20}
                          height={20}
                        />
                      ))}
                    </span>
                    <span>
                      <strong>{surface.label}</strong>
                      <small>{surface.caption}</small>
                    </span>
                  </button>
                ))}
              </div>
            </div>

            <SurfacePreview surface={currentSurface} />
          </div>
        </section>

        <section className="lp-section lp-showcase-section" id="showcase">
          <div className="lp-section-inner lp-reveal">
            <div className="lp-showcase-head">
              <div>
                <p className="lp-section-kicker">What people build</p>
                <h2 className="lp-section-title">
                  A pod for every workflow your team runs on spreadsheets.
                </h2>
                <p className="lp-section-subhead">
                  These are the architectures. Fork one and ship.
                </p>
              </div>
              <a
                className="lp-button secondary"
                href="/docs"
                target="_blank"
                rel="noreferrer"
              >
                Browse starter kits
              </a>
            </div>

            <div className="lp-showcase-grid">
              {showcaseCards.map((card) => {
                const isFlipped = flippedCards.has(card.tag);
                return (
                  <button
                    className={isFlipped ? "is-flipped" : ""}
                    key={card.tag}
                    type="button"
                    onClick={() => {
                      setFlippedCards((current) => {
                        const next = new Set(current);
                        if (next.has(card.tag)) next.delete(card.tag);
                        else next.add(card.tag);
                        return next;
                      });
                    }}
                  >
                    <span className="lp-showcase-card-inner">
                      <span className="lp-showcase-face">
                        <span className="lp-tag">{card.tag}</span>
                        <span className="lp-showcase-claim">{card.claim}</span>
                        <span className="lp-flip-hint">Click for flow</span>
                      </span>
                      <span className="lp-showcase-face lp-showcase-back">
                        <span className="lp-tag">Flow</span>
                        <span className="lp-flow-list">{card.flow}</span>
                      </span>
                    </span>
                  </button>
                );
              })}
            </div>
          </div>
        </section>

        <section
          className="lp-section lp-stack-section"
          id="replace"
          aria-labelledby="stack-title"
        >
          <div className="lp-section-inner lp-reveal">
            <StackComparison value={stackCompare} onChange={setStackCompare} />
          </div>
        </section>

        <MachineAtWork />

        <section className="lp-section lp-quickstart-section" id="quickstart">
          <div className="lp-section-inner lp-quickstart-grid lp-reveal">
            <div>
              <p className="lp-section-kicker">Try it</p>
              <h2 className="lp-section-title">
                Running in under <span>five minutes.</span>
              </h2>
              <p className="lp-section-subhead">
                Install the CLI, create a pod, import a starter kit. You get
                the whole system you just scrolled through - agents, data,
                workflows, and an app - running locally.
              </p>
              <div className="lp-quick-points">
                <div>
                  <strong>1</strong> Install the CLI
                </div>
                <div>
                  <strong>2</strong> Create a pod from a starter kit
                </div>
                <div>
                  <strong>3</strong> Run the app and inspect the workflow
                </div>
              </div>
            </div>

            <div className="lp-terminal" aria-label="Quickstart commands">
              <div>
                <span>lemma quickstart</span>
                <button
                  type="button"
                  onClick={() => {
                    void navigator.clipboard?.writeText(
                      "uv tool install lemma-terminal\nlemma pods create support-ops\nlemma pods import ./support-inbox\nlemma apps deploy support-ops",
                    );
                  }}
                >
                  Copy
                </button>
              </div>
              <TypingTerminal />
            </div>
          </div>
        </section>

        <section className="lp-section lp-footer-cta" id="github">
          <div className="lp-footer-box lp-reveal">
            <h2>Start with a pod.</h2>
            <p>Open-source. Self-host locally or run with Lemma.</p>
            <div className="lp-actions">
              <a
                className="lp-button primary"
                href={githubUrl}
                target="_blank"
                rel="noreferrer"
              >
                <GithubLogo aria-hidden className="lp-gh-icon" />
                Star on GitHub
              </a>
              <a
                className="lp-button secondary"
                href="/docs"
                target="_blank"
                rel="noreferrer"
              >
                Browse the docs
              </a>
            </div>
          </div>
        </section>
      </main>
    </div>
  );
}
