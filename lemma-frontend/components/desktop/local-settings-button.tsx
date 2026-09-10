"use client";

import { toast } from "sonner";
import { Button } from "@/components/ui/button";
import { Settings } from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import { Tooltip, TooltipContent, TooltipProvider, TooltipTrigger } from "@/components/ui/tooltip";
import { useDesktopBridge } from "@/lib/desktop/local-capabilities";

type LocalSettingsButtonProps = {
  variant?: "row" | "rail";
  page?: "overview" | "ai" | "sharing" | "diagnostics";
  className?: string;
  onOpen?: () => void;
};

export function LocalSettingsButton({
  variant = "row",
  page = "overview",
  className,
  onOpen,
}: LocalSettingsButtonProps) {
  // `useDesktopBridge` rather than this component's own check of
  // `__LEMMA_DESKTOP__.mode`. That field is baked into the initialization
  // script when the window is *built*, so on a first run that chooses local
  // without recreating the webview it still reads unset -- and the button that
  // opens Local settings did not render in the one session where someone had
  // just set local up. See `readDesktopBridge` for the same bug's first
  // appearance.
  const visible = useDesktopBridge();

  if (!visible) return null;

  const open = async () => {
    onOpen?.();
    try {
      await window.__TAURI__?.core?.invoke?.("open_control_center", { page });
    } catch (error) {
      // The workspace is a remote origin to Tauri, so this call depends on a
      // capability naming this exact origin. When that is missing the promise
      // rejects and the button would otherwise look simply broken.
      toast.error(
        `Couldn't open Local settings: ${error instanceof Error ? error.message : String(error)}`,
      );
    }
  };

  if (variant === "rail") {
    return (
      <TooltipProvider>
        <Tooltip>
          <TooltipTrigger asChild>
            <Button
              type="button"
              variant="quiet"
              size="icon"
              onClick={() => void open()}
              className={cn("lemma-sidebar-rail-icon relative", className)}
              aria-label="Open Local settings"
            >
              <Settings className="h-4 w-4" />
              <span
                className="absolute right-1 top-1 h-1.5 w-1.5 rounded-full bg-[var(--state-success)] ring-1 ring-[var(--pod-shell-bg)]"
                aria-hidden="true"
              />
            </Button>
          </TooltipTrigger>
          <TooltipContent side="right">Local settings</TooltipContent>
        </Tooltip>
      </TooltipProvider>
    );
  }

  return (
    <Button
      type="button"
      variant="quiet"
      size="sm"
      onClick={() => void open()}
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
    </Button>
  );
}
