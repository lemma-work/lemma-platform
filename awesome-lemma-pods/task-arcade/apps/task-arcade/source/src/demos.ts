import type { CineScript } from "./cinematic";

// ─────────────────────────────────────────────────────────────────────────────
// DEMO LIBRARY
//
// Each entry is a hardcoded cinematic walkthrough of a user flow. They run over
// deterministic mock state (never the live pod) so they replay identically and
// are safe to show anyone. To add a new demo: append a CineScript here. The beat
// fields map onto the control vocabulary exposed by App.tsx (the `do` action
// names) and the sound bank (the `sfx` names) — see SFX_URLS / `cine` in App.tsx.
//
//   do   — named control actions fired at the beat's time
//   zoom — camera move on the world stage (scale + transform-origin)
//   hero — large center caption (hold:null keeps it up for the closing line)
//   lower— lower-third narration
//   sfx  — sound effect name to fire on the beat
// ─────────────────────────────────────────────────────────────────────────────

export const DEMOS: CineScript[] = [
  {
    id: "world-tour",
    title: "The world tour",
    blurb: "Tasks become buildings — finish, approve, or send to rubble. The 90-second pitch.",
    tag: "Overview",
    duration: 21.6,
    beats: [
      {
        t: 0,
        do: ["reset", "world"],
        zoom: { scale: 1.05, origin: "50% 52%", dur: 6, ease: "sine.inOut" },
        hero: { text: "Your tasks, as a world.", hold: 2.0 },
      },
      {
        t: 3.2,
        do: ["spotlightFeature"],
        zoom: { scale: 1.16, origin: "42% 50%", dur: 1.6 },
        lower: { text: "Every building is a task someone finished.", hold: 2.2 },
      },
      {
        t: 6.6,
        do: ["spotlightHero", "placeHero"],
        zoom: { scale: 1.22, origin: "55% 56%", dur: 1.5 },
        lower: { text: "Clear a task → drop a build into the world.", hold: 2.4 },
        sfx: "shimmer",
      },
      {
        t: 10.6,
        do: ["approveHero"],
        zoom: { scale: 1.1, origin: "55% 54%", dur: 1.2 },
        lower: { text: "Your manager approves it — now it's permanent.", hold: 2.4 },
        sfx: "shimmer",
      },
      {
        t: 14.4,
        do: ["spotlightReject", "demolishReject"],
        zoom: { scale: 1.18, origin: "64% 46%", dur: 1.3 },
        lower: { text: "…or sends it to rubble. Proof of work you can see.", hold: 2.2 },
      },
      {
        t: 18.0,
        do: ["unspotlight"],
        zoom: { scale: 1.0, origin: "50% 52%", dur: 1.8 },
        hero: { text: "The week IS the world.", hold: null },
      },
    ],
  },

  {
    id: "approve-reject",
    title: "Approve, reject, prove it",
    blurb: "Manager works the review queue — one build solidifies, one collapses to rubble, then pull back to the world.",
    tag: "Review",
    duration: 11,
    beats: [
      // Set the stage: seed the world, drop a second pending build, open the Review queue.
      {
        t: 0,
        do: ["reset", "placeHero", "review"],
        zoom: { scale: 1.08, origin: "55% 50%", dur: 2.2 },
      },
      // Spotlight the pending row (3D build + review row both highlight via selection).
      { t: 0.4, do: ["spotlightHero"] },
      // Approve → scaffold solidifies + particle burst; sprint total ticks up. Chime + bar whoosh.
      {
        t: 1.6,
        do: ["approveHero"],
        zoom: { scale: 1.2, origin: "55% 50%", dur: 1.2 },
        lower: { text: "Your manager approves it — now it's permanent.", hold: 2.8 },
        sfx: ["chime", "whoosh"],
      },
      // Spotlight a different pending build, then reject → collapse to rubble + dust.
      { t: 5.2, do: ["spotlightReject"], zoom: { scale: 1.18, origin: "64% 46%", dur: 1.0 } },
      {
        t: 5.9,
        do: ["demolishReject"],
        lower: { text: "…or demolishes it. Proof of work you can see.", hold: 2.2 },
        sfx: "crumble",
      },
      // Pull back to the whole world (sprint bar now reads higher) for the closing line.
      {
        t: 8.6,
        do: ["unspotlight", "world"],
        zoom: { scale: 1.0, origin: "50% 52%", dur: 1.9 },
        hero: { text: "The week doesn't get a report. The week IS the world.", hold: null },
        sfx: "swell",
      },
    ],
  },
];
