import { useLayoutEffect, useRef, useState } from "react";
import gsap from "gsap";
import { RotateCcw, X } from "lucide-react";

// The director drives app state through these callbacks; it never touches data itself,
// so the cinematic stays deterministic and replays identically.
export type CineControls = {
  reset: () => void;
  world: () => void;
  review: () => void;
  spotlightFeature: () => void;
  spotlightHero: () => void;
  spotlightReject: () => void;
  unspotlight: () => void;
  placeHero: () => void;
  approveHero: () => void;
  demolishReject: () => void;
};

// ── Script format ───────────────────────────────────────────────────────────
// A demo is data, not code: a list of timed "beats". The director below
// interprets these against a control map + sound bank, so new walkthroughs are
// authored by adding a CineScript (see demos.ts) — no engine changes needed.
export type CineZoom = { scale: number; origin?: string; dur?: number; ease?: string };
export type CineBeat = {
  /** start time in seconds from the top of the timeline */
  t: number;
  /** named control actions to fire at t (keys of the controls map passed in) */
  do?: string[];
  /** camera zoom on the world stage */
  zoom?: CineZoom;
  /** large center caption; hold=null keeps it up (used for the closing line) */
  hero?: { text: string; hold?: number | null };
  /** lower-third narration caption */
  lower?: { text: string; hold?: number };
  /** sound effect name(s) to play at t (keys of the sound bank) */
  sfx?: string | string[];
};
export type CineScript = {
  id: string;
  title: string;
  /** one-line description shown on the library card */
  blurb: string;
  /** total runtime in seconds — controls when the end controls reveal */
  duration: number;
  /** optional short tag shown on the card (e.g. "Onboarding") */
  tag?: string;
  beats: CineBeat[];
};

export function CinematicDirector({
  active,
  script,
  stageRef,
  controls,
  playSfx,
  onExit,
}: {
  active: boolean;
  script: CineScript | null;
  stageRef: React.RefObject<HTMLElement | null>;
  controls: Record<string, () => void>;
  playSfx: (name: string) => void;
  onExit: () => void;
}) {
  const root = useRef<HTMLDivElement>(null);
  const heroRef = useRef<HTMLParagraphElement>(null);
  const lowerRef = useRef<HTMLParagraphElement>(null);
  const vignRef = useRef<HTMLDivElement>(null);
  const tlRef = useRef<gsap.core.Timeline | null>(null);
  const [ended, setEnded] = useState(false);

  useLayoutEffect(() => {
    if (!active || !script) return;
    setEnded(false);
    const stage = stageRef.current;

    const ctx = gsap.context(() => {
      const h = heroRef.current!;
      const l = lowerRef.current!;
      gsap.set([h, l], { autoAlpha: 0 });
      if (stage) gsap.set(stage, { transformOrigin: "50% 52%", scale: 1 });

      const tl = gsap.timeline({
        defaults: { ease: "power3.out" },
        onComplete: () => setEnded(true),
      });
      tlRef.current = tl;

      // The frame always opens with a soft vignette fade so captions read clearly.
      tl.fromTo(vignRef.current, { autoAlpha: 0 }, { autoAlpha: 1, duration: 1.0 }, 0);

      const zoomAt = (t: number, z: CineZoom) => {
        if (!stage) return;
        tl.to(
          stage,
          {
            scale: z.scale,
            transformOrigin: z.origin ?? "50% 52%",
            duration: z.dur ?? 1.4,
            ease: z.ease ?? "power2.inOut",
            overwrite: "auto",
          },
          t,
        );
      };

      // hold=null → caption stays up (used for the closing line)
      const heroAt = (t: number, txt: string, hold: number | null) => {
        tl.call(() => { h.textContent = txt; }, undefined, t)
          .fromTo(
            h,
            { autoAlpha: 0, scale: 0.7, y: 30, filter: "blur(10px)" },
            { autoAlpha: 1, scale: 1, y: 0, filter: "blur(0px)", duration: 0.85, ease: "back.out(1.4)" },
            t,
          );
        if (hold != null) {
          tl.to(h, { autoAlpha: 0, scale: 1.06, y: -22, filter: "blur(6px)", duration: 0.5, ease: "power2.in" }, t + hold);
        }
      };

      const lowerAt = (t: number, txt: string, hold: number) => {
        tl.call(() => { l.textContent = txt; }, undefined, t)
          .fromTo(l, { autoAlpha: 0, y: 30 }, { autoAlpha: 1, y: 0, duration: 0.5 }, t)
          .to(l, { autoAlpha: 0, y: -16, duration: 0.4, ease: "power2.in" }, t + hold);
      };

      // ── Interpret the script ────────────────────────────────────────────
      for (const b of script.beats) {
        if (b.do) for (const name of b.do) tl.call(() => controls[name]?.(), undefined, b.t);
        if (b.sfx) {
          const cues = Array.isArray(b.sfx) ? b.sfx : [b.sfx];
          for (const s of cues) tl.call(() => playSfx(s), undefined, b.t);
        }
        if (b.zoom) zoomAt(b.t, b.zoom);
        if (b.hero) heroAt(b.t, b.hero.text, b.hero.hold === undefined ? 2.0 : b.hero.hold);
        if (b.lower) lowerAt(b.t, b.lower.text, b.lower.hold ?? 2.2);
      }

      // Empty tail so the closing beat holds before Replay/Exit reveal.
      tl.to({}, { duration: 0.01 }, script.duration);
    }, root);

    return () => {
      ctx.revert();
      if (stage) gsap.set(stage, { clearProps: "transform" });
    };
  }, [active, script?.id]); // eslint-disable-line react-hooks/exhaustive-deps

  if (!active || !script) return null;

  const replay = () => {
    setEnded(false);
    controls.reset?.();
    tlRef.current?.restart();
  };

  return (
    <div className="cine-layer" ref={root}>
      <div className="cine-vignette" ref={vignRef} />
      <div className="cine-cap-wrap">
        <p className="cine-hero" ref={heroRef} />
      </div>
      <p className="cine-lower" ref={lowerRef} />
      <div className="cine-controls">
        {ended && (
          <button className="cine-btn" type="button" onClick={replay}>
            <RotateCcw size={15} /> Replay
          </button>
        )}
        <button className="cine-btn cine-btn--ghost" type="button" onClick={onExit}>
          <X size={15} /> Exit
        </button>
      </div>
    </div>
  );
}
