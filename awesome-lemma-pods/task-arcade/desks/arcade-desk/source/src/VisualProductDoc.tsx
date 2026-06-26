import {
  Bell,
  Bot,
  CalendarClock,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ClipboardCheck,
  Flag,
  Hammer,
  Megaphone,
  Pause,
  Play,
  Send,
  Sparkles,
  Users,
} from "lucide-react";
import { useEffect, useMemo, useState } from "react";

type SlideId = "vision" | "campaign" | "hover" | "approval" | "workflows" | "schedules" | "surfaces" | "recap";

type Slide = {
  id: SlideId;
  kicker: string;
  title: string;
  narrative: string;
  points: string[];
  metric: string;
};

const slides: Slide[] = [
  {
    id: "vision",
    kicker: "Product story",
    title: "Team focus becomes a shared world.",
    narrative:
      "A marketing-ready roadmap for a team ritual: launch a campaign, contribute focus blocks, approve the real work, and share the world as proof of momentum.",
    points: ["Visual first", "Campaign-led", "Built for team rituals"],
    metric: "Roadmap",
  },
  {
    id: "campaign",
    kicker: "Feature 01",
    title: "Launch a weekly campaign.",
    narrative:
      "The leader opens a fresh world for Launch Week, Content Sprint, Bug Bash, or Deep Work Friday. A blank map becomes a shared challenge.",
    points: ["Campaign title, dates, team, goal", "Fresh world board", "Progress starts at zero"],
    metric: "0 / 480 min",
  },
  {
    id: "hover",
    kicker: "Feature 02",
    title: "Hover any block to meet the teammate behind it.",
    narrative:
      "Every block carries identity: who added it, what they focused on, how long they worked, and whether the contribution is trusted.",
    points: ["Contributor name", "Intention and tag", "Approval status"],
    metric: "4 teammates",
  },
  {
    id: "approval",
    kicker: "Feature 03",
    title: "Approve it into the world or demolish it.",
    narrative:
      "Pending blocks appear as scaffolds. A leader approves the block to finish construction or demolishes it when the work should not count.",
    points: ["Pending scaffold", "Approve builds", "Reject demolishes"],
    metric: "4 pending",
  },
  {
    id: "workflows",
    kicker: "Lemma workflows",
    title: "The approval moment is a workflow.",
    narrative:
      "The showpiece is backed by a human-in-the-loop process: submit block, assign leader review, run decision, update the world.",
    points: ["FORM assigned to leader", "Function writes review", "World state updates"],
    metric: "approve_focus_block",
  },
  {
    id: "schedules",
    kicker: "Lemma schedules",
    title: "The campaign moves without babysitting.",
    narrative:
      "Scheduled prompts and milestone checks keep the team loop alive: weekly launch nudges, daily digests, and goal celebrations.",
    points: ["Weekly campaign prompt", "Daily digest", "Milestone broadcast"],
    metric: "3 automations",
  },
  {
    id: "surfaces",
    kicker: "Lemma surfaces",
    title: "Slack spreads the momentum.",
    narrative:
      "The desk is where the world lives. Slack is where the team sees progress, milestones, approvals, and the final recap.",
    points: ["Launch message", "Milestone update", "Weekly recap"],
    metric: "Slack-ready",
  },
  {
    id: "recap",
    kicker: "Marketing close",
    title: "The final world becomes a shareable artifact.",
    narrative:
      "At the end of the week, the campaign is not a report. It is a visible proof-of-work moment the team can celebrate.",
    points: ["Top contributors", "Approved minutes", "Final world snapshot"],
    metric: "92% goal",
  },
];

const teammates = [
  { name: "Maya", role: "Design", color: "#f0a92e", block: "Launch page wireframe", minutes: "45 min" },
  { name: "Arjun", role: "Engineering", color: "#42be65", block: "Checkout polish", minutes: "60 min" },
  { name: "Nina", role: "Growth", color: "#a878e4", block: "Email campaign draft", minutes: "30 min" },
  { name: "Leo", role: "Ops", color: "#4f90df", block: "Partner list cleanup", minutes: "30 min" },
];

function MiniWorld({ mode }: { mode: SlideId }) {
  const showHover = mode === "hover";
  const showApproval = mode === "approval";
  const showRecap = mode === "recap" || mode === "surfaces";

  return (
    <div className={`deck-world deck-world--${mode}`}>
      <div className="deck-world__top">
        <div>
          <span>Launch Week</span>
          <strong>Campaign World</strong>
        </div>
        <div className="deck-world__bar">
          <i style={{ width: mode === "campaign" ? "8%" : showRecap ? "92%" : "58%" }} />
        </div>
      </div>
      <div className="deck-map">
        {Array.from({ length: 24 }, (_, index) => (
          <span className="deck-tile" key={index} />
        ))}
        <span className="deck-block deck-block--tree" style={{ left: "34%", top: "32%" }}>
          <b style={{ background: teammates[0].color }}>M</b>
        </span>
        <span className="deck-block deck-block--house" style={{ left: "52%", top: "43%" }}>
          <b style={{ background: teammates[1].color }}>A</b>
        </span>
        <span className="deck-block deck-block--crystal" style={{ left: "67%", top: "34%" }}>
          <b style={{ background: teammates[2].color }}>N</b>
        </span>
        <span
          className={showApproval ? "deck-block deck-block--scaffold is-live" : "deck-block deck-block--scaffold"}
          style={{ left: "43%", top: "59%" }}
        >
          <b style={{ background: teammates[3].color }}>L</b>
        </span>
        <span className="deck-block deck-block--rubble" style={{ left: "69%", top: "63%" }} />
        {showHover && (
          <div className="deck-hover-card">
            <div>
              <span style={{ background: teammates[1].color }}>A</span>
              <div>
                <strong>Arjun Mehta</strong>
                <small>Engineering · Approved</small>
              </div>
            </div>
            <p>Checkout polish</p>
            <footer>
              <span>60 min</span>
              <span>Deep work</span>
            </footer>
          </div>
        )}
        {showApproval && (
          <div className="deck-review-card">
            <strong>Review Leo's block</strong>
            <p>Partner list cleanup · 30 min</p>
            <div>
              <button type="button">
                <CheckCircle2 size={16} /> Approve
              </button>
              <button type="button">
                <Hammer size={16} /> Demolish
              </button>
            </div>
          </div>
        )}
      </div>
    </div>
  );
}

function WorkflowVisual() {
  return (
    <div className="deck-diagram">
      {[
        ["Submit", "Member adds focus block"],
        ["FORM", "Leader review assigned"],
        ["Function", "review_focus_block"],
        ["World", "Build or demolish"],
      ].map(([label, detail], index) => (
        <article key={label}>
          <span>{String(index + 1).padStart(2, "0")}</span>
          <strong>{label}</strong>
          <p>{detail}</p>
        </article>
      ))}
    </div>
  );
}

function ScheduleVisual() {
  return (
    <div className="deck-calendar">
      <section>
        <CalendarClock size={26} />
        <strong>Mon 9:00</strong>
        <span>Start campaign prompt</span>
      </section>
      <section>
        <Bell size={26} />
        <strong>Daily 5:30</strong>
        <span>Campaign digest</span>
      </section>
      <section>
        <Sparkles size={26} />
        <strong>On milestone</strong>
        <span>Broadcast progress</span>
      </section>
    </div>
  );
}

function SurfaceVisual() {
  return (
    <div className="deck-surface">
      <div className="deck-surface__world">
        <MiniWorld mode="surfaces" />
      </div>
      <div className="deck-chat">
        <header>
          <Send size={18} />
          <strong>Team channel</strong>
        </header>
        <div className="chat-line strong">
          <span />
          <p>Launch Week is 58% built</p>
        </div>
        <div className="chat-line">
          <span />
          <p>12 blocks approved · 4 pending review</p>
        </div>
        <button type="button">Open campaign world</button>
      </div>
    </div>
  );
}

function TeamStrip() {
  return (
    <div className="deck-team-strip">
      {teammates.map((mate) => (
        <article key={mate.name}>
          <span style={{ background: mate.color }}>{mate.name[0]}</span>
          <div>
            <strong>{mate.name}</strong>
            <small>{mate.role}</small>
          </div>
          <p>{mate.block}</p>
          <em>{mate.minutes}</em>
        </article>
      ))}
    </div>
  );
}

function SlideVisual({ slide }: { slide: Slide }) {
  if (slide.id === "workflows") return <WorkflowVisual />;
  if (slide.id === "schedules") return <ScheduleVisual />;
  if (slide.id === "surfaces") return <SurfaceVisual />;
  return <MiniWorld mode={slide.id} />;
}

export default function VisualProductDoc() {
  const [activeIndex, setActiveIndex] = useState(0);
  const [isAutoplaying, setIsAutoplaying] = useState(true);
  const activeSlide = slides[activeIndex];

  const nextSlide = () => setActiveIndex((current) => (current + 1) % slides.length);
  const previousSlide = () => setActiveIndex((current) => (current - 1 + slides.length) % slides.length);

  useEffect(() => {
    if (!isAutoplaying) return;
    const timer = window.setInterval(nextSlide, 5200);
    return () => window.clearInterval(timer);
  }, [isAutoplaying]);

  useEffect(() => {
    const onKeyDown = (event: KeyboardEvent) => {
      if (event.key === "ArrowRight") {
        setIsAutoplaying(false);
        nextSlide();
      }
      if (event.key === "ArrowLeft") {
        setIsAutoplaying(false);
        previousSlide();
      }
    };
    window.addEventListener("keydown", onKeyDown);
    return () => window.removeEventListener("keydown", onKeyDown);
  }, []);

  const slideLabel = useMemo(() => `${activeIndex + 1} / ${slides.length}`, [activeIndex]);

  return (
    <main className="product-deck">
      <section className="deck-stage">
        <div className="deck-copy">
          <span className="deck-kicker">{activeSlide.kicker}</span>
          <h1>{activeSlide.title}</h1>
          <p>{activeSlide.narrative}</p>
          <ul>
            {activeSlide.points.map((point) => (
              <li key={point}>{point}</li>
            ))}
          </ul>
        </div>

        <div className="deck-visual">
          <SlideVisual slide={activeSlide} />
        </div>

        <footer className="deck-footer">
          <div className="deck-metric">
            <strong>{activeSlide.metric}</strong>
            <span>{slideLabel}</span>
          </div>
          <div className="deck-controls">
            <button type="button" aria-label="Previous roadmap slide" onClick={() => { setIsAutoplaying(false); previousSlide(); }}>
              <ChevronLeft size={18} />
            </button>
            <button type="button" onClick={() => setIsAutoplaying((value) => !value)}>
              {isAutoplaying ? <Pause size={18} /> : <Play size={18} />}
              {isAutoplaying ? "Pause" : "Play"}
            </button>
            <button type="button" aria-label="Next roadmap slide" onClick={() => { setIsAutoplaying(false); nextSlide(); }}>
              <ChevronRight size={18} />
            </button>
          </div>
        </footer>
      </section>

      <nav className="deck-rail" aria-label="Roadmap slides">
        {slides.map((slide, index) => (
          <button
            className={activeIndex === index ? "active" : ""}
            key={slide.id}
            type="button"
            onClick={() => {
              setActiveIndex(index);
              setIsAutoplaying(false);
            }}
          >
            <span>{String(index + 1).padStart(2, "0")}</span>
            <strong>{slide.kicker}</strong>
          </button>
        ))}
      </nav>

      <aside className="deck-context">
        <div>
          <Megaphone size={18} />
          <span>Marketing proof points</span>
        </div>
        <TeamStrip />
        <div className="deck-layer-row">
          <span><ClipboardCheck size={16} /> Workflow</span>
          <span><CalendarClock size={16} /> Schedule</span>
          <span><Bot size={16} /> Surface</span>
          <span><Flag size={16} /> Recap</span>
        </div>
      </aside>
    </main>
  );
}
