"use client";

import { useEffect, useState } from "react";

import { Logo } from "@/components/brand/logo";
import { WaitingScreen } from "@/components/shared/loading";
import { ResourceIdentity } from "@/components/shared/resource-identity";
import { Button } from "@/components/ui/button";
import { Check, Hash, MessageCircle } from "@/components/ui/icons";
import { trackOnboardingStep } from "@/lib/analytics/onboarding";
import { LEM_SEED } from "@/lib/identity/seeded-identity";
import { DEFAULT_RESPONDER_NAME } from "@/lib/utils/agents";
import { cn } from "@/lib/utils";

/**
 * The four screens a new account sees while its workspace is being made.
 *
 * Provisioning takes a few seconds and used to spend them on a spinner, after
 * which the welcome door opened on somebody who had never been told what the
 * product is. This spends them instead: one claim per screen, a picture that
 * shows the claim happening rather than a paragraph describing it, and nothing
 * to decide. The door at the end is unchanged.
 *
 * The four are the four things worth knowing on day one, in the order they
 * build on each other — an agent does a real job; that job becomes an app;
 * your people join the app with their own permissions; and the agent reaches
 * you wherever you already are. One job runs through all four frames so the
 * set reads as a story rather than a feature list.
 *
 * Rules the frames are held to, because the failure mode of a tour is a
 * paragraph nobody reads: one sentence per frame and never two; the picture
 * carries the claim; Skip is always visible; and a frame that has started is
 * never cut short by provisioning finishing early — readiness only changes
 * the label on the last button.
 */
export type FirstRunTourFrameId =
  | "tour_agent"
  | "tour_app"
  | "tour_people"
  | "tour_channels";

export interface FirstRunTourFrame {
  id: FirstRunTourFrameId;
  /** The one sentence. Never a second. */
  claim: string;
}

export const FIRST_RUN_TOUR_FRAMES: readonly FirstRunTourFrame[] = [
  {
    id: "tour_agent",
    claim:
      "Give an agent a real job. It reads, builds, schedules, and asks before anything goes out.",
  },
  {
    id: "tour_app",
    claim:
      "Ask for an app and get one: a screen for the job, with the agent working behind it.",
  },
  {
    id: "tour_people",
    claim:
      "Add your people. Same agents, same apps, same data, each with their own permissions.",
  },
  {
    id: "tour_channels",
    claim:
      "Talk to your agents from WhatsApp, Slack, or Telegram, and approve right there.",
  },
];

export type FirstRunTourStatus = "working" | "ready";

interface FirstRunTourProps {
  /** Whether the workspace behind the tour exists yet. */
  status: FirstRunTourStatus;
  /**
   * The person is done here — they pressed through the last frame or Skip.
   * Called once. The caller goes in when it can; until then this shows the
   * wait, which is the only time a spinner is on screen at all.
   */
  onOpen: () => void;
}

export function FirstRunTour({ status, onOpen }: FirstRunTourProps) {
  const [index, setIndex] = useState(0);
  const [finished, setFinished] = useState(false);
  const frame = FIRST_RUN_TOUR_FRAMES[index];
  const count = FIRST_RUN_TOUR_FRAMES.length;
  const onLast = index === count - 1;

  useEffect(() => {
    if (!finished) trackOnboardingStep(frame.id);
  }, [frame.id, finished]);

  useEffect(() => {
    if (finished) onOpen();
  }, [finished, onOpen]);

  const back = () => setIndex((current) => Math.max(0, current - 1));
  const next = () => {
    if (onLast) setFinished(true);
    else setIndex((current) => Math.min(count - 1, current + 1));
  };

  useEffect(() => {
    if (finished) return;
    const onKey = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") next();
      if (event.key === "ArrowLeft") back();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
    // `next` and `back` close over `onLast`, which is derived from `index`.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [finished, index]);

  if (finished) {
    return (
      <div className="first-run-tour" data-testid="first-run-tour">
        <WaitingScreen
          title="Setting up your workspace"
          description={`One moment. ${DEFAULT_RESPONDER_NAME} is nearly ready for you.`}
          className="m-auto w-full max-w-xl"
        />
      </div>
    );
  }

  const ready = status === "ready";
  const Art = FRAME_ART[frame.id];

  return (
    <div className="first-run-tour" data-testid="first-run-tour">
      <header className="first-run-tour-top">
        <Logo size="sm" />
        <Button variant="quiet" size="sm" type="button" onClick={() => setFinished(true)}>
          Skip
        </Button>
      </header>

      <div className="first-run-tour-body">
        <section key={frame.id} className="first-run-tour-frame" aria-live="polite">
          <Art />
          <h1 className="first-run-tour-claim">{frame.claim}</h1>
        </section>

        <div className="first-run-tour-controls">
          <div className="first-run-tour-dots" role="tablist" aria-label="Screens">
            {FIRST_RUN_TOUR_FRAMES.map((candidate, position) => (
              <Button
                key={candidate.id}
                variant="quiet"
                size="icon"
                type="button"
                role="tab"
                aria-selected={position === index}
                aria-label={`Screen ${position + 1} of ${count}`}
                className="first-run-tour-dot"
                onClick={() => setIndex(position)}
              />
            ))}
          </div>
          <div className="first-run-tour-actions">
            <Button variant="quiet" size="md" type="button" disabled={index === 0} onClick={back}>
              Back
            </Button>
            <Button
              variant="primary"
              size="md"
              type="button"
              className="first-run-tour-continue"
              onClick={next}
            >
              {onLast && ready ? "Open my workspace" : "Continue"}
            </Button>
          </div>
        </div>
      </div>

      <footer className="first-run-tour-foot">
        <span className={cn("first-run-tour-status", ready && "is-ready")} role="status">
          <span className="first-run-tour-led" aria-hidden="true" />
          {ready ? "Your workspace is ready" : "Setting up your workspace"}
        </span>
        <span className="first-run-tour-counter">
          {index + 1} of {count}
        </span>
      </footer>
    </div>
  );
}

/* ── The pictures ──────────────────────────────────────────────────────────
 *
 * Illustrations, not controls: every button-shaped thing below is a span, so
 * nothing here can take focus or fire. The copy is a single worked example —
 * one invoices job — carried through all four frames on purpose.
 */

/** Lem, peeking around the edge of a window the way a mascot does. */
function Lem({ placement }: { placement: "top-right" | "bottom-left" | "bottom-right" }) {
  return (
    <ResourceIdentity
      seed={LEM_SEED}
      label={DEFAULT_RESPONDER_NAME}
      kind="being"
      size={56}
      state="running"
      className={cn("first-run-tour-lem", `is-${placement}`)}
    />
  );
}

function WindowBar({
  title,
  children,
}: {
  title: React.ReactNode;
  children?: React.ReactNode;
}) {
  return (
    <div className="first-run-mock-bar">
      <span className="first-run-mock-lights" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      {title}
      {children ? <span className="first-run-mock-bar-end">{children}</span> : null}
    </div>
  );
}

function MockButton({
  tone = "secondary",
  children,
}: {
  tone?: "primary" | "secondary" | "quiet";
  children: React.ReactNode;
}) {
  return (
    <span className={cn("first-run-mock-button", tone === "primary" && "is-primary", tone === "quiet" && "is-quiet")}>
      {children}
    </span>
  );
}

function Tick() {
  return (
    <span className="first-run-mock-check" aria-hidden="true">
      <Check className="h-4 w-4" />
    </span>
  );
}

function AgentArt() {
  return (
    <div className="first-run-tour-scene">
      <Lem placement="top-right" />
      <div className="first-run-mock-window">
        <WindowBar title={DEFAULT_RESPONDER_NAME} />
        <div className="first-run-mock-chat">
          <p className="first-run-mock-bubble is-you">
            Pull every invoice from my inbox into a table, and chase the overdue ones every Monday.
          </p>
          <div className="first-run-mock-bubble is-agent">
            <div className="first-run-mock-steps">
              <span className="first-run-mock-step">
                <Tick />
                <span>
                  Read 14 emails from billing@
                  <span className="first-run-mock-step-meta">2 min</span>
                </span>
              </span>
              <span className="first-run-mock-step">
                <Tick />
                <span>
                  Created the table <b>Invoices</b>, 14 rows
                </span>
              </span>
              <span className="first-run-mock-step">
                <Tick />
                <span>Drafted 3 chasers for the overdue ones</span>
              </span>
              <span className="first-run-mock-step">
                <Tick />
                <span>Scheduled: every Monday, 09:00</span>
              </span>
            </div>
            <div className="first-run-mock-ask">
              <span className="first-run-mock-pill is-ask">Waiting for you</span>
              <span>Send the 3 chasers now?</span>
              <span className="first-run-mock-ask-actions">
                <MockButton tone="primary">Send</MockButton>
                <MockButton>Show me first</MockButton>
              </span>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}

function AppArt() {
  return (
    <div className="first-run-tour-scene">
      <Lem placement="bottom-left" />
      <div className="first-run-mock-window">
        <WindowBar
          title={
            <>
              Invoices
              <span className="first-run-mock-pill is-accent">{DEFAULT_RESPONDER_NAME} behind it</span>
            </>
          }
        >
          Built from &ldquo;make this an app&rdquo;
        </WindowBar>
        <div className="first-run-mock-stats">
          <div className="first-run-mock-stat">
            <span className="first-run-mock-stat-label">Outstanding</span>
            <span className="first-run-mock-stat-value">$6,050</span>
          </div>
          <div className="first-run-mock-stat">
            <span className="first-run-mock-stat-label">Overdue</span>
            <span className="first-run-mock-stat-value">
              1<small>chaser drafted</small>
            </span>
          </div>
          <div className="first-run-mock-stat">
            <span className="first-run-mock-stat-label">Paid this month</span>
            <span className="first-run-mock-stat-value">$12,400</span>
          </div>
        </div>
        <table className="first-run-mock-table">
          <thead>
            <tr>
              <th scope="col">Client</th>
              <th scope="col">Amount</th>
              <th scope="col">Due</th>
              <th scope="col">
                <span className="sr-only">Status</span>
              </th>
            </tr>
          </thead>
          <tbody>
            <tr>
              <td className="is-name">Halden Press</td>
              <td className="is-amount">$4,200</td>
              <td>Sep 3</td>
              <td className="is-end">
                <span className="first-run-mock-pill is-ask">Overdue</span>
                <MockButton>Chase</MockButton>
              </td>
            </tr>
            <tr>
              <td className="is-name">Marrow Health</td>
              <td className="is-amount">$1,850</td>
              <td>Sep 12</td>
              <td className="is-end">
                <span className="first-run-mock-pill is-quiet">Sent</span>
              </td>
            </tr>
            <tr>
              <td className="is-name">Okapi Studio</td>
              <td className="is-amount">$960</td>
              <td>Sep 1</td>
              <td className="is-end">
                <span className="first-run-mock-pill is-ok">Paid</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  );
}

const PEOPLE: ReadonlyArray<{ initials: string; name: string; note: string; role: string; hue: 0 | 1 | 4 }> = [
  { initials: "You", name: "You", note: "Approve, edit, invite. You see everything.", role: "Owner", hue: 0 },
  { initials: "MK", name: "Maya Kapoor", note: "Approved 2 chasers this morning.", role: "Editor", hue: 4 },
  { initials: "PN", name: "Priya Nair", note: "Sees invoices, nothing else in the workspace.", role: "Viewer", hue: 1 },
];

function PeopleArt() {
  return (
    <div className="first-run-tour-scene is-narrow">
      <Lem placement="top-right" />
      <div className="first-run-mock-window">
        <WindowBar title="Invoices" />
        <div className="first-run-mock-people">
          {PEOPLE.map((person) => (
            <div key={person.name} className={cn("first-run-mock-person", `lm-identity-hue-${person.hue}`)}>
              <span className="first-run-mock-person-avatar" aria-hidden="true">
                {person.initials}
              </span>
              <span>
                <span className="first-run-mock-person-name">{person.name}</span>
                <span className="first-run-mock-person-note">{person.note}</span>
              </span>
              <span className="first-run-mock-pill is-quiet">{person.role}</span>
            </div>
          ))}
          <div className="first-run-mock-invite">
            <span className="first-run-mock-field">arjun@halden.press</span>
            <MockButton tone="primary">Invite</MockButton>
          </div>
        </div>
      </div>
    </div>
  );
}

function ChannelsArt() {
  return (
    <div className="first-run-tour-scene">
      <Lem placement="bottom-right" />
      <div className="first-run-mock-channels">
        <div className="first-run-mock-window">
          <WindowBar
            title={
              <>
                <span className="first-run-mock-pill is-whatsapp">
                  <MessageCircle className="h-3 w-3" />
                  WhatsApp
                </span>
                {DEFAULT_RESPONDER_NAME}
              </>
            }
          >
            <span className="first-run-mock-time">Mon 09:00</span>
          </WindowBar>
          <div className="first-run-mock-thread">
            <span className="first-run-mock-thread-in">
              Halden Press is 7 days overdue on $4,200. Send the chaser?
            </span>
            <span className="first-run-mock-thread-out">
              Yes, send it
              <Check className="h-3.5 w-3.5" aria-hidden="true" />
            </span>
            <span className="first-run-mock-thread-in">Sent. I will tell you when they reply.</span>
          </div>
        </div>
        <div className="first-run-mock-window">
          <WindowBar
            title={
              <>
                <span className="first-run-mock-pill is-slack">
                  <Hash className="h-3 w-3" />
                  finance
                </span>
                Slack
              </>
            }
          >
            <span className="first-run-mock-time">Mon 09:01</span>
          </WindowBar>
          <div className="first-run-mock-thread">
            <div className="first-run-mock-message lm-identity-hue-0">
              <span className="first-run-mock-message-avatar" aria-hidden="true">
                {DEFAULT_RESPONDER_NAME.slice(0, 1)}
              </span>
              <span>
                <span className="first-run-mock-message-who">
                  {DEFAULT_RESPONDER_NAME}
                  <span className="first-run-mock-message-time">09:01</span>
                </span>
                <span className="first-run-mock-message-body">
                  Weekly numbers are ready: <b>$6,050 outstanding, 1 overdue</b>. Post them here?
                </span>
                <span className="first-run-mock-message-actions">
                  <MockButton tone="primary">Post</MockButton>
                  <MockButton>Edit first</MockButton>
                </span>
              </span>
            </div>
            <div className="first-run-mock-message lm-identity-hue-4">
              <span className="first-run-mock-message-avatar" aria-hidden="true">
                MK
              </span>
              <span>
                <span className="first-run-mock-message-who">
                  Maya
                  <span className="first-run-mock-message-time">09:03</span>
                </span>
                <span className="first-run-mock-message-body">Post it. Nice.</span>
              </span>
            </div>
          </div>
        </div>
      </div>
      <p className="first-run-tour-aside">Also Telegram, Teams, and email.</p>
    </div>
  );
}

const FRAME_ART: Record<FirstRunTourFrameId, () => React.ReactElement> = {
  tour_agent: AgentArt,
  tour_app: AppArt,
  tour_people: PeopleArt,
  tour_channels: ChannelsArt,
};
