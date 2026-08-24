"use client";

import Image from "next/image";
import Link from "next/link";
import { GithubLogo } from "@/components/ui/icons";
import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { SiteFooter, SiteHeader } from "./site-chrome";
import type { SurfaceMode } from "./landing-data";
import { githubUrl, surfaceModes } from "./landing-data";
import { WorkSurfaceStrip } from "./landing-animations";
import { HeroBuildsCollage } from "./hero-builds-collage";
import { JourneySection } from "./landing-journey";
import {
  BuildSection,
  ExamplesSection,
  SharedSection,
} from "./landing-story";
import { PodSystemSection } from "./landing-pod-system";
import { SurfacePreview } from "./landing-surfaces";

/**
 * The closer reads against the visitor's own clock — "build it this morning"
 * only lands if it is actually their morning. Buckets are ordered by the hour
 * they end at.
 */
const DAY_PARTS = [
  { endsAt: 5, build: "tonight", by: "by morning" },
  { endsAt: 11, build: "this morning", by: "by lunch" },
  { endsAt: 17, build: "this afternoon", by: "by evening" },
  { endsAt: 22, build: "this evening", by: "by morning" },
  { endsAt: 24, build: "tonight", by: "by morning" },
] as const;

type DayPart = (typeof DAY_PARTS)[number];

/** Afternoon is the neutral render: the server has no idea what time it is
 *  where the reader is, and guessing would mismatch on hydration. */
const DEFAULT_DAY_PART: DayPart = DAY_PARTS[2];

function dayPartFor(hour: number): DayPart {
  return DAY_PARTS.find((part) => hour < part.endsAt) ?? DEFAULT_DAY_PART;
}

/* The whole point is that server and client disagree, which is what
   useSyncExternalStore's third argument is for. Entries are module constants,
   so the snapshot is reference-stable and never re-renders in a loop. */
const NEVER_CHANGES = () => () => {};

export default function LandingPage() {
  const [activeSurface, setActiveSurface] =
    useState<SurfaceMode["key"]>("slack");
  const rootRef = useRef<HTMLDivElement>(null);

  const dayPart = useSyncExternalStore(
    NEVER_CHANGES,
    () => dayPartFor(new Date().getHours()),
    () => DEFAULT_DAY_PART,
  );

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

  const currentSurface =
    surfaceModes.find((surface) => surface.key === activeSurface) ??
    surfaceModes[0];

  return (
    <div className="lp-react" ref={rootRef}>
      <SiteHeader />

      <main>
        {/* §1 — thesis in three lines */}
        <section className="lp-hero" aria-labelledby="hero-title">
          <div className="lp-hero-copy">
            <p className="lp-eyebrow">
              <span className="lp-eyebrow-badge">
                <GithubLogo aria-hidden className="lp-gh-icon" />
                Open source
              </span>
              Multiplayer AI and Apps
            </p>
            <h1 className="lp-hero-headline" id="hero-title">
              Shared Apps and Agents.
            </h1>
            <p className="lp-subhead">
              Your team, your agents, one context layer — scoped to each
              person.
            </p>

            <WorkSurfaceStrip />

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
          </div>

          <section
            className="lp-hero-builds"
            aria-label="What teams build with Lemma"
          >
            <HeroBuildsCollage />
          </section>
        </section>

        {/* §2 — what "shared" means, demonstrated */}
        <SharedSection />

        {/* §3 — build, deploy, invite, scope, use */}
        <JourneySection />

        {/* §4 — the pod as a working system */}
        <PodSystemSection />

        {/* §5 — every surface, one pod */}
        <section
          className="lp-section lp-surfaces-section"
          id="surfaces"
          aria-labelledby="surfaces-title"
        >
          <div className="lp-section-inner">
            <div className="lp-surfaces-head lp-reveal">
              <p className="lp-section-kicker">Surfaces</p>
              <h2 className="lp-section-title" id="surfaces-title">
                However the work arrives, it lands in{" "}
                <span>the same pod.</span>
              </h2>
              <p className="lp-section-subhead">
                Nine surfaces, including ChatGPT and Claude. Each one knows who
                is asking, and writes to the same records.
              </p>
            </div>

            <div
              className="lp-surface-rail lp-reveal"
              role="tablist"
              aria-label="Surfaces"
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
                  {surface.logos.map((logo) => (
                    <Image
                      key={logo.label}
                      src={logo.src}
                      alt=""
                      width={18}
                      height={18}
                    />
                  ))}
                  {surface.label}
                </button>
              ))}
            </div>

            <div className="lp-surfaces-stage lp-reveal">
              {/* Remounting on key replays the scene for the chosen surface
                  instead of looping it forever and emptying out. */}
              <SurfacePreview key={currentSurface.key} surface={currentSurface} />

              <aside className="lp-surface-effect">
                <p className="lp-surface-effect-lead">
                  {currentSurface.headlineLead} {currentSurface.headlineTail}
                </p>
                <p className="lp-surface-effect-body">{currentSurface.body}</p>

                <p className="lp-surface-effect-label">
                  What it did to the pod
                </p>
                <ul>
                  {currentSurface.effect.map((line) => (
                    <li key={line}>{line}</li>
                  ))}
                </ul>

                <p className="lp-surface-effect-foot">
                  A surface, not a copy. The records, workflows, permissions,
                  and audit trail stay in one place.
                </p>
              </aside>
            </div>
          </div>
        </section>

        {/* §6 — the agent you already use builds it */}
        <BuildSection />

        {/* §7 — examples to install and remix */}
        <ExamplesSection />

        {/* §8 — close */}
        <section className="lp-section lp-footer-cta" id="github">
          <div className="lp-footer-box lp-reveal">
            <h2>
              Build it {dayPart.build}. Have everyone using it {dayPart.by}.
            </h2>
            <p>
              Open source. Running on your laptop in five minutes, with the
              coding agent you already have open.
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
          </div>
        </section>
      </main>

      {/* The page used to stop dead at the CTA — no docs, no licence, no way
          out except the two buttons. */}
      <SiteFooter />
    </div>
  );
}
