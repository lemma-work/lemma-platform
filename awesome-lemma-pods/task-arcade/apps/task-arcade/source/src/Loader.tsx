import { useEffect, useRef } from "react";
import gsap from "gsap";

// Kinetic verb loader — ported from cta-light.html.
// Plays ASSIGN → CLEAR → BUILD → APPROVE on a loop and is cut off the
// moment the parent unmounts it (i.e. when the page has loaded).

const VERBS = [
  { text: "ASSIGN", color: "#5e30a0" },
  { text: "CLEAR", color: "#165280" },
  { text: "BUILD", color: "#236b38" },
  { text: "APPROVE", color: "#9a6c08" },
];

const SPARK_COLORS = [
  "#9a6c08", "#14201a", "#C18A1A", "#14201a",
  "#9a6c08", "#e0aa55", "#9a6c08", "#14201a",
  "#C18A1A", "#14201a", "#e0aa55", "#9a6c08",
];

const SPARK_ANGLES = [0, 30, 60, 90, 120, 150, 180, 210, 240, 270, 300, 330];
const SPARK_DISTANCES = [200, 230, 170, 210, 250, 185, 195, 220, 165, 235, 190, 215];

export function Loader({ label = "Loading" }: { label?: string }) {
  const root = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const el = root.current;
    if (!el) return;

    const verbs = Array.from(el.querySelectorAll<HTMLElement>(".ta-verb"));
    const sparks = Array.from(el.querySelectorAll<HTMLElement>(".ta-spark"));
    if (verbs.length < 4) return;

    const [assignEl, clearEl, buildEl, approveEl] = verbs;

    gsap.set(verbs, { xPercent: -50, yPercent: -50, autoAlpha: 0 });
    gsap.set(sparks, { autoAlpha: 0 });

    const tl = gsap.timeline({ repeat: -1, repeatDelay: 0.55, paused: true });

    // ── Verb cuts ───────────────────────────────────────────────
    // ASSIGN
    tl.fromTo(assignEl,
      { autoAlpha: 0, scale: 1.18 },
      { autoAlpha: 1, scale: 1, duration: 0.13, ease: "power3.out" }, 0.25);
    tl.to(assignEl,
      { autoAlpha: 0, scale: 0.88, duration: 0.1, ease: "power2.in" }, 0.68);

    // CLEAR
    tl.fromTo(clearEl,
      { autoAlpha: 0, scale: 1.18 },
      { autoAlpha: 1, scale: 1, duration: 0.13, ease: "power3.out" }, 0.74);
    tl.to(clearEl,
      { autoAlpha: 0, scale: 0.88, duration: 0.1, ease: "power2.in" }, 1.18);

    // BUILD
    tl.fromTo(buildEl,
      { autoAlpha: 0, scale: 1.18 },
      { autoAlpha: 1, scale: 1, duration: 0.13, ease: "power3.out" }, 1.24);
    tl.to(buildEl,
      { autoAlpha: 0, scale: 0.88, duration: 0.1, ease: "power2.in" }, 1.68);

    // APPROVE (amber slam)
    tl.fromTo(approveEl,
      { autoAlpha: 0, scale: 1.22 },
      { autoAlpha: 1, scale: 1, duration: 0.15, ease: "back.out(1.4)" }, 1.74);

    // ── Spark burst on APPROVE ──────────────────────────────────
    SPARK_ANGLES.forEach((deg, i) => {
      const rad = (deg * Math.PI) / 180;
      const dx = Math.cos(rad) * SPARK_DISTANCES[i];
      const dy = Math.sin(rad) * SPARK_DISTANCES[i];
      tl.fromTo(sparks[i],
        { autoAlpha: 1, x: 0, y: 0, scale: 1.2 },
        { autoAlpha: 0, x: dx, y: dy, scale: 0, duration: 0.65, ease: "power2.out" },
        1.76 + i * 0.012);
    });

    // APPROVE hold → fade
    tl.to(approveEl,
      { autoAlpha: 0, y: -26, duration: 0.38, ease: "power2.in" }, 2.32);

    tl.play();

    return () => {
      tl.kill();
      gsap.set([...verbs, ...sparks], { clearProps: "all" });
    };
  }, []);

  return (
    <div
      ref={root}
      style={{
        position: "fixed",
        inset: 0,
        display: "grid",
        placeItems: "center",
        overflow: "hidden",
        background:
          "radial-gradient(ellipse 100% 75% at 50% 38%, #fff6e4 0%, #FAF8F5 42%, #ede8df 100%)",
        fontFamily: '"Anton", "Avenir Next", sans-serif',
        zIndex: 9999,
      }}
    >
      {/* Kinetic verbs */}
      {VERBS.map((v) => (
        <div
          key={v.text}
          className="ta-verb"
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            fontSize: "clamp(64px, 15vw, 196px)",
            letterSpacing: "-0.02em",
            lineHeight: 1,
            textTransform: "uppercase",
            color: v.color,
            whiteSpace: "nowrap",
            willChange: "transform, opacity",
          }}
        >
          {v.text}
        </div>
      ))}

      {/* Spark particles (amber palette, dark on light) */}
      {SPARK_COLORS.map((bg, i) => (
        <span
          key={i}
          className="ta-spark"
          style={{
            position: "absolute",
            top: "50%",
            left: "50%",
            width: 8,
            height: 8,
            marginLeft: -4,
            marginTop: -4,
            borderRadius: "50%",
            background: bg,
            willChange: "transform, opacity",
          }}
        />
      ))}

      {/* Loading label */}
      <div
        style={{
          position: "absolute",
          bottom: "12%",
          left: 0,
          right: 0,
          textAlign: "center",
          fontFamily: '"Avenir Next", "Trebuchet MS", sans-serif',
          fontSize: 15,
          fontWeight: 700,
          letterSpacing: "0.16em",
          textTransform: "uppercase",
          color: "#5a6b62",
        }}
      >
        {label}
      </div>
    </div>
  );
}
