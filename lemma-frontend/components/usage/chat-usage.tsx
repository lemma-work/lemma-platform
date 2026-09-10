"use client";

import { useEffect, useRef } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";

// No standing allowance meter in the composer: on almost every render it would
// spend a permanent control to report that nothing is wrong. The one state
// worth interrupting for is the limit that just stopped a turn, and that
// arrives here as an error code — no polling needed to notice it. The full
// picture lives at /profile/usage.
export function ChatUsage({
  organizationId,
  running,
  conversationId,
  errorCode,
}: {
  organizationId?: string;
  running: boolean;
  conversationId: string | null;
  errorCode?: string | null;
}) {
  const queryClient = useQueryClient();
  const previous = useRef({ running, errorCode, conversationId });
  // A finished turn and a fresh error both move the numbers, so a usage screen
  // open beside this chat refetches instead of showing a pre-turn total.
  useEffect(() => {
    const last = previous.current;
    if (
      (last.running && !running) ||
      (errorCode && errorCode !== last.errorCode)
    ) {
      void queryClient.invalidateQueries({ queryKey: ["usage"] });
    }
    previous.current = { running, errorCode, conversationId };
  }, [running, errorCode, conversationId, queryClient]);
  if (errorCode !== "USAGE_LIMIT_EXCEEDED") return null;
  const detailsHref = `/profile/usage${organizationId ? `?organizationId=${encodeURIComponent(organizationId)}` : ""}`;
  return (
    <Link href={detailsHref} className="text-xs text-[var(--state-error)]">
      Usage limit reached · View reset times
    </Link>
  );
}
