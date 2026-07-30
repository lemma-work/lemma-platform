"use client";

import { useEffect, useState } from "react";
import { Settings } from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";

declare global {
  interface Window {
    __TAURI__?: {
      core?: {
        invoke?: (command: string, args?: Record<string, unknown>) => Promise<unknown>;
      };
    };
  }
}

type LocalSettingsButtonProps = {
  variant?: "row" | "rail";
  page?: "overview" | "models" | "sharing" | "diagnostics";
  className?: string;
  onOpen?: () => void;
};

export function LocalSettingsButton({
  variant = "row",
  page = "overview",
  className,
  onOpen,
}: LocalSettingsButtonProps) {
  const [visible, setVisible] = useState(false);

  useEffect(() => {
    setVisible(
      window.__LEMMA_DESKTOP__?.mode === "local"
      && typeof window.__TAURI__?.core?.invoke === "function",
    );
  }, []);

  if (!visible) return null;

  const open = () => {
    onOpen?.();
    void window.__TAURI__?.core?.invoke?.("open_control_center", { page });
  };

  if (variant === "rail") {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <button
              type="button"
              onClick={open}
              className={cn("lemma-sidebar-rail-icon relative", className)}
              aria-label="Open Local settings"
            >
              <Settings className="h-4 w-4" />
              <span
                className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-[var(--state-success)] ring-1 ring-[var(--pod-shell-bg)]"
                aria-hidden="true"
              />
            </button>
          </TooltipTrigger>
          <TooltipContent side="right">Local settings</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return (
    <button
      type="button"
      onClick={open}
      className={cn(
        "lemma-sidebar-row lemma-sidebar-row-sm custom-focus-ring relative w-full text-[var(--text-secondary)]",
        className,
      )}
    >
      <Settings className="h-4 w-4 shrink-0" />
      <span className="min-w-0 flex-1 truncate text-left">Local settings</span>
      <span
        className="h-1.5 w-1.5 shrink-0 rounded-full bg-[var(--state-success)]"
        aria-label="Local stack ready"
      />
    </button>
  );
}
