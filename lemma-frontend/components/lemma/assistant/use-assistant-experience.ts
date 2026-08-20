"use client";

// Self-contained hooks extracted from assistant-experience.tsx. Only hooks that
// take explicit inputs and return values (no closing over the component's mutable
// locals) live here; the rest stay in AssistantExperienceView to preserve behavior.

import { useCallback, useEffect, useState } from "react";

/** A ticking clock that only runs while something needs it.
 *
 * Elapsed-time labels ("Working for 12s") used to tick a `setInterval` at the
 * top of the assistant view, re-rendering the entire transcript every second.
 * The tick belongs to the leaf that shows the number — a pill, an indicator —
 * so this hook lives there and re-renders one small component instead. */
export function useNowMs(active: boolean): number {
  // Initialized at mount, which is when the label's owner appears — fresh by
  // construction, so activating the tick needs no synchronous reset.
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const interval = window.setInterval(() => setNowMs(Date.now()), 1000);
    return () => clearInterval(interval);
  }, [active]);
  return nowMs;
}

export function useControllableDraft(
  controlledValue: string | undefined,
  onChange: ((value: string) => void) | undefined,
): [string, (value: string) => void] {
  const [uncontrolledValue, setUncontrolledValue] = useState("");
  const isControlled = typeof controlledValue === "string";

  const setValue = useCallback((nextValue: string) => {
    if (!isControlled) {
      setUncontrolledValue(nextValue);
    }
    onChange?.(nextValue);
  }, [isControlled, onChange]);

  return [isControlled ? controlledValue : uncontrolledValue, setValue];
}
