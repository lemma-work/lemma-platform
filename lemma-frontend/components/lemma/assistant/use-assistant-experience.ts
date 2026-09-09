"use client";

// Self-contained hooks extracted from assistant-experience.tsx. Only hooks that
// take explicit inputs and return values (no closing over the component's mutable
// locals) live here; the rest stay in AssistantExperienceView to preserve behavior.

import { useCallback, useEffect, useRef, useState } from "react";

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

/** How long typing has to pause before the draft is written to localStorage. */
export const DRAFT_PERSIST_DEBOUNCE_MS = 400;

export function draftStorageKey(conversationId: string | null): string {
  return `lemma:draft:${conversationId ?? "new"}`;
}

function writeDraft(key: string, draft: string) {
  if (draft) {
    localStorage.setItem(key, draft);
  } else {
    localStorage.removeItem(key);
  }
}

/** Keeps the composer's draft in localStorage, one entry per conversation.
 *
 * Restores on conversation change, and persists on a debounce: `localStorage`
 * is synchronous, and writing once per keystroke put a main-thread write
 * between the keypress and the frame that draws it. A draft only has to
 * survive a reload, so a pause in typing is soon enough.
 *
 * Everything below exists because a deferred write can be cancelled by the very
 * thing it is meant to record. The returned function is how a send says the
 * draft is gone for good, and the two flushes are how a pending write survives
 * the composer moving on.
 */
export function useDraftPersistence(
  activeConversationId: string | null,
  draft: string,
  setDraft: (value: string) => void,
): () => void {
  const restoredRef = useRef(false);
  const pendingRef = useRef<{ key: string; draft: string } | null>(null);

  useEffect(() => {
    // Whatever is still pending belongs to the conversation being left: this
    // dependency change is about to cancel its timer, so it goes out now. That
    // includes the empty draft a send leaves behind — losing it is what left a
    // sent message under `lemma:draft:new`, to be restored into the composer
    // the next time a new chat was opened.
    const pending = pendingRef.current;
    if (pending) {
      writeDraft(pending.key, pending.draft);
      pendingRef.current = null;
    }
    restoredRef.current = true;
    setDraft(localStorage.getItem(draftStorageKey(activeConversationId)) ?? "");
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [activeConversationId]);

  useEffect(() => {
    if (restoredRef.current) {
      restoredRef.current = false;
      return;
    }
    const key = draftStorageKey(activeConversationId);
    // Recorded only alongside a scheduled write, so a flush never writes a
    // value that is already in storage — nor one belonging to another key.
    pendingRef.current = { key, draft };
    const timer = window.setTimeout(() => {
      writeDraft(key, draft);
      pendingRef.current = null;
    }, DRAFT_PERSIST_DEBOUNCE_MS);
    return () => {
      window.clearTimeout(timer);
    };
  }, [draft, activeConversationId]);

  // A draft still sitting in the debounce when the composer unmounts would
  // otherwise be lost, so the last one is written out on the way down.
  useEffect(() => () => {
    const pending = pendingRef.current;
    if (pending) writeDraft(pending.key, pending.draft);
  }, []);

  // Sending does not wait for the debounce. The message has left the composer,
  // and the conversation id is about to change under it — by the time the
  // deferred write would have run, its timer is gone and its key is stale.
  return useCallback(() => {
    const key = draftStorageKey(activeConversationId);
    pendingRef.current = null;
    writeDraft(key, "");
  }, [activeConversationId]);
}
