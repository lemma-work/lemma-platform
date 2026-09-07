"use client";

import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useQueryClient } from "@tanstack/react-query";
import { BarChart3 } from "@/components/ui/icons";
import { Button } from "@/components/ui/button";
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/ui/popover";
import { useMyUsageLimits } from "@/lib/hooks/use-usage";
import { UsageAllowances } from "./usage-allowances";

export function ChatUsage({
  organizationId,
  running,
  conversationId,
  errorCode,
  ownCredentials = false,
  enabled = true,
}: {
  organizationId?: string;
  running: boolean;
  conversationId: string | null;
  errorCode?: string | null;
  ownCredentials?: boolean;
  enabled?: boolean;
}) {
  const [open, setOpen] = useState(false);
  const queryClient = useQueryClient();
  const previous = useRef({ running, errorCode, conversationId });
  const quotaError = errorCode === "USAGE_LIMIT_EXCEEDED";
  const limits = useMyUsageLimits(organizationId, {
    enabled: enabled && (open || quotaError),
    poll: open && running,
  });
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
  const detailsHref = `/profile/usage${organizationId ? `?organizationId=${encodeURIComponent(organizationId)}` : ""}`;
  const nearLimit = limits.data?.windows.some(
    (window) => window.used_percent >= (limits.data?.warning_percent ?? 100),
  );
  const color = limits.isError
    ? ""
    : limits.data?.allowed === false
      ? "text-[var(--state-error)]"
      : nearLimit
        ? "text-[var(--state-warning)]"
        : "";
  return (
    <div className="flex items-center gap-2">
      <Popover open={open} onOpenChange={setOpen}>
        <PopoverTrigger asChild>
          <Button
            variant="quiet"
            size="icon"
            className={`h-8 w-8 ${color}`}
            disabled={!enabled}
            aria-label="View usage limits"
            title="Usage limits"
          >
            <BarChart3 className="h-4 w-4" />
          </Button>
        </PopoverTrigger>
        <PopoverContent
          collisionPadding={16}
          align="start"
          side="top"
          className="w-80 max-w-[calc(100vw-2rem)] p-5"
        >
          <UsageAllowances
            data={limits.data}
            loading={limits.isPending}
            error={limits.error}
            retry={() => void limits.refetch()}
          />
          {ownCredentials ? (
            <p className="mt-4 text-xs text-[var(--text-secondary)]">
              Your selected model uses your own credentials and does not spend
              this allowance.
            </p>
          ) : null}
          <div className="mt-5 border-t border-[var(--border-subtle)] pt-3">
            <Link
              href={detailsHref}
              className="text-sm text-[var(--text-secondary)] hover:text-[var(--text-primary)]"
              onClick={() => setOpen(false)}
            >
              View usage details →
            </Link>
          </div>
        </PopoverContent>
      </Popover>
      {quotaError ? (
        <Link href={detailsHref} className="text-xs text-[var(--state-error)]">
          Usage limit reached · View reset times
        </Link>
      ) : null}
    </div>
  );
}
