import Image from "next/image";
import { useEffect, useRef, useState } from "react";
import type { TerminalLine } from "./landing-data";
import { fullTerminalLines, terminalScript } from "./landing-data";

/**
 * Surfaces only — the places you *use* a pod. Claude Code and Codex are what
 * *builds* it, and they own §6; putting them under "Use it from" made one logo
 * mean two different things on the same page.
 */
const workSurfaces = [
  {
    label: "Slack",
    src: "/landing-page/app-logos/slack.svg",
  },
  {
    label: "Teams",
    src: "/landing-page/app-logos/teams.svg",
  },
  {
    label: "Telegram",
    src: "/landing-page/app-logos/telegram.svg",
  },
  {
    label: "WhatsApp",
    src: "/landing-page/app-logos/whatsapp.svg",
  },
] as const;

export function WorkSurfaceStrip() {
  return (
    <div
      className="lp-work-surfaces"
      aria-label="Use Lemma from Slack, Microsoft Teams, Telegram, WhatsApp, or anywhere you work"
    >
      <span className="lp-work-surfaces-lead">Use it from</span>
      <span className="lp-work-surface-list" aria-hidden="true">
        {workSurfaces.map((surface) => (
          <span className="lp-work-surface" key={surface.label}>
            <Image src={surface.src} alt="" width={18} height={18} />
            <span>{surface.label}</span>
          </span>
        ))}
      </span>
      <span className="lp-work-surfaces-anywhere">
        <i aria-hidden="true">+</i>
        anywhere you work
      </span>
    </div>
  );
}

export function TypingTerminal() {
  const [lines, setLines] = useState<TerminalLine[]>([]);
  const [typed, setTyped] = useState<string | null>(null);
  const [isDone, setIsDone] = useState(false);
  const preRef = useRef<HTMLPreElement>(null);

  useEffect(() => {
    const pre = preRef.current;
    if (!pre) return;

    let cancelled = false;
    const timeouts: ReturnType<typeof setTimeout>[] = [];
    const wait = (ms: number) =>
      new Promise<void>((resolve) => {
        timeouts.push(setTimeout(resolve, ms));
      });

    const run = async () => {
      for (const step of terminalScript) {
        setTyped("");
        for (let i = 1; i <= step.command.length; i += 1) {
          await wait(24);
          if (cancelled) return;
          setTyped(step.command.slice(0, i));
        }
        await wait(260);
        if (cancelled) return;
        setTyped(null);
        setLines((current) => [
          ...current,
          { kind: "command", text: step.command },
        ]);
        for (const text of step.output) {
          await wait(90);
          if (cancelled) return;
          setLines((current) => [...current, { kind: "output", text }]);
        }
        await wait(320);
        if (cancelled) return;
      }
      setIsDone(true);
    };

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          observer.disconnect();
          if (window.matchMedia("(prefers-reduced-motion: reduce)").matches) {
            setLines(fullTerminalLines);
            setIsDone(true);
            return;
          }
          void run();
        }
      },
      { threshold: 0.4 },
    );
    observer.observe(pre);
    return () => {
      cancelled = true;
      observer.disconnect();
      for (const timeout of timeouts) clearTimeout(timeout);
    };
  }, []);

  return (
    <pre ref={preRef} aria-label="Quickstart terminal session">
      <code>
        {lines.map((line, index) => (
          <span className="lp-term-line" key={`${line.text}-${index}`}>
            {line.kind === "command" ? (
              <>
                <span>$</span> {line.text}
              </>
            ) : (
              line.text
            )}
            {"\n"}
          </span>
        ))}
        {typed !== null ? (
          <span className="lp-term-line">
            <span>$</span> {typed}
            <i className="lp-term-cursor" aria-hidden="true" />
          </span>
        ) : null}
        {isDone ? <i className="lp-term-cursor" aria-hidden="true" /> : null}
      </code>
    </pre>
  );
}
