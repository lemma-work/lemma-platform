"use client";

import { forwardRef, type ComponentPropsWithoutRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import { Badge } from "@/components/ui/badge";
import type {
  AssistantConversationListItem,
  AssistantConversationRenderArgs,
  LemmaAssistantRadius,
} from "./assistant-types";

export type AssistantSurfaceTone = "default" | "subtle" | "flat";
export type AssistantThemeMode = "auto" | "light" | "dark";

const RADIUS_MAP: Record<string, Record<string, string>> = {
  none: { shell: "rounded-none", item: "rounded-none", bubble: "rounded-none", inline: "rounded-none" },
  sm: { shell: "rounded-sm", item: "rounded-sm", bubble: "rounded-sm", inline: "rounded-sm" },
  md: { shell: "rounded-md", item: "rounded-md", bubble: "rounded-md", inline: "rounded-md" },
  lg: { shell: "rounded-lg", item: "rounded-md", bubble: "rounded-lg", inline: "rounded-md" },
  xl: { shell: "rounded-xl", item: "rounded-lg", bubble: "rounded-xl", inline: "rounded-lg" },
};

function assistantRadius(radius: LemmaAssistantRadius, kind: "shell" | "item" | "bubble" | "inline"): string {
  return RADIUS_MAP[radius]?.[kind] ?? RADIUS_MAP.lg[kind];
}

export function conversationStatusDotColor(status?: string | null): string {
  const s = (status || "").toLowerCase();
  if (s === "running" || s === "active") return "status-dot-active";
  if (s === "completed" || s === "done") return "status-dot-success";
  if (s === "error" || s === "failed") return "status-dot-error";
  return "status-dot-warning";
}

export function relativeTimeAgo(dateStr?: string | null): string {
  if (!dateStr) return "";
  const date = new Date(dateStr);
  if (isNaN(date.getTime())) return "";
  const now = Date.now();
  const diffMs = now - date.getTime();
  const seconds = Math.floor(diffMs / 1000);
  if (seconds < 60) return "just now";
  const minutes = Math.floor(seconds / 60);
  if (minutes < 60) return `${minutes}m ago`;
  const hours = Math.floor(minutes / 60);
  if (hours < 24) return `${hours}h ago`;
  const days = Math.floor(hours / 24);
  return `${days}d ago`;
}

export interface AssistantThemeScopeProps extends ComponentPropsWithoutRef<"div"> {
  children: ReactNode;
  theme?: AssistantThemeMode;
}

export const AssistantThemeScope = forwardRef<HTMLDivElement, AssistantThemeScopeProps>(function AssistantThemeScope({
  className,
  children,
  theme = "auto",
  ...props
}, ref) {
  return (
    <div
      ref={ref}
      data-lemma-theme={theme}
      className={cn("flex h-full min-h-0 w-full flex-col", theme === "dark" && "dark", className)}
      {...props}
    >
      {children}
    </div>
  );
});

export interface AssistantHeaderProps extends Omit<ComponentPropsWithoutRef<"div">, "title"> {
  title: ReactNode;
  subtitle?: ReactNode;
  badge?: ReactNode;
  leadingControls?: ReactNode;
  controls?: ReactNode;
  tone?: AssistantSurfaceTone;
  compact?: boolean;
}

export interface AssistantMessageViewportProps extends ComponentPropsWithoutRef<"div"> {
  innerClassName?: string;
  children: ReactNode;
}

export const AssistantMessageViewport = forwardRef<HTMLDivElement, AssistantMessageViewportProps>(function AssistantMessageViewport({
  className,
  innerClassName,
  children,
  ...props
}, ref) {
  return (
    <div
      ref={ref}
      className={cn("min-h-0 flex-1 overflow-y-auto bg-[var(--pod-main-bg)] px-4 py-6 [font-family:var(--font-landing-sans),var(--font-body-family)] [overflow-anchor:none] sm:px-6 lg:px-8", className)}
      {...props}
    >
      <div className={cn("mx-auto flex w-full max-w-4xl flex-col gap-6", innerClassName)}>
        {children}
      </div>
    </div>
  );
});

export interface AssistantShellLayoutProps extends ComponentPropsWithoutRef<"div"> {
  sidebar?: ReactNode;
  sidebarVisible?: boolean;
  main: ReactNode;
  radius?: LemmaAssistantRadius;
}

export const AssistantShellLayout = forwardRef<HTMLDivElement, AssistantShellLayoutProps>(function AssistantShellLayout({
  sidebar,
  sidebarVisible = false,
  main,
  radius = "lg",
  className,
  ...props
}, ref) {
  const hasSidebar = !!sidebar;

  return (
    <div
      ref={ref}
      className={cn(
        "flex h-full min-h-0 w-full flex-col gap-3",
        hasSidebar && sidebarVisible && "lg:grid lg:grid-cols-[minmax(16rem,24rem)_minmax(0,1fr)] lg:items-stretch",
        assistantRadius(radius, "shell"),
        className,
      )}
      {...props}
    >
      {sidebar && sidebarVisible ? (
        <div className={cn("min-h-0 overflow-hidden border border-[color:color-mix(in_srgb,var(--border-subtle)_60%,transparent)] bg-[color:color-mix(in_srgb,var(--surface-2)_25%,transparent)] shadow-[var(--shadow-sm)]", assistantRadius(radius, "shell"))}>
          {sidebar}
        </div>
      ) : null}
      {main}
    </div>
  );
});

export const AssistantHeader = forwardRef<HTMLDivElement, AssistantHeaderProps>(function AssistantHeader({
  title,
  subtitle,
  badge,
  leadingControls,
  controls,
  tone = "subtle",
  compact = false,
  className,
  ...props
}, ref) {
  return (
    <div
      ref={ref}
      data-tone={tone}
      className={cn(
        "lemma-assistant-header flex shrink-0 items-center justify-between border-b border-[color:color-mix(in_srgb,var(--border-subtle)_60%,transparent)]",
        compact ? "gap-2 px-3 py-2" : "gap-3 px-4 py-3 sm:px-6",
        tone === "default" && "bg-[color:color-mix(in_srgb,var(--card-bg)_95%,transparent)]",
        tone === "subtle" && "bg-[color:color-mix(in_srgb,var(--bg-canvas)_95%,transparent)]",
        tone === "flat" && "bg-transparent",
        className,
      )}
      {...props}
    >
      <div className={cn("flex min-w-0 items-center", compact ? "gap-2" : "gap-3")}>
        {leadingControls}
        {badge ? (
          <span className={cn("flex shrink-0 items-center justify-center rounded-lg bg-[var(--action-primary)] text-[var(--text-on-brand)]", compact ? "size-7" : "size-9")}>
            {badge}
          </span>
        ) : null}
        <div className="min-w-0">
          <h3 className={cn("truncate font-semibold tracking-tight text-[var(--text-primary)]", compact ? "text-sm" : "text-lg")}>{title}</h3>
          {subtitle && !compact ? (
            <p className="truncate text-sm text-[var(--text-secondary)]">{subtitle}</p>
          ) : null}
        </div>
      </div>
      {controls ? (
        <div className={cn("flex shrink-0 items-center", compact ? "gap-1" : "gap-2")}>{controls}</div>
      ) : null}
    </div>
  );
});

export interface AssistantConversationListProps extends Omit<ComponentPropsWithoutRef<"aside">, "title"> {
  conversations: AssistantConversationListItem[];
  activeConversationId: string | null;
  onSelectConversation: (conversationId: string) => void;
  onNewConversation?: () => void;
  renderConversationLabel?: (args: AssistantConversationRenderArgs) => ReactNode;
  title?: ReactNode;
  newLabel?: ReactNode;
  radius?: LemmaAssistantRadius;
}

export const AssistantConversationList = forwardRef<HTMLElement, AssistantConversationListProps>(function AssistantConversationList({
  conversations,
  activeConversationId,
  onSelectConversation,
  onNewConversation,
  renderConversationLabel,
  title = "Conversations",
  newLabel = "New",
  radius = "lg",
  className,
  ...props
}, ref) {
  return (
    <aside ref={ref} className={cn("flex h-full min-h-0 flex-col overflow-hidden border border-[color:color-mix(in_srgb,var(--border-subtle)_60%,transparent)] bg-[color:color-mix(in_srgb,var(--surface-2)_25%,transparent)]", assistantRadius(radius, "shell"), className)} {...props}>
      <div className="border-b border-[color:color-mix(in_srgb,var(--border-subtle)_60%,transparent)] px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-[var(--text-primary)]">{title}</div>
            <div className="mt-0.5 text-xs text-[var(--text-secondary)]">
              {conversations.length} total
            </div>
          </div>
          {onNewConversation ? (
            <Button
              type="button"
              variant="outline"
              size="sm"
              onClick={onNewConversation}
              className="h-8 px-3 text-sm"
            >
              {newLabel}
            </Button>
          ) : null}
        </div>
      </div>
      <div className="flex min-h-0 flex-1 flex-col gap-2 overflow-y-auto p-3">
        {conversations.map((conversation) => {
          const isActive = conversation.id === activeConversationId;
          return (
            <button
              key={conversation.id}
              type="button"
              onClick={() => onSelectConversation(conversation.id)}
              aria-current={isActive ? "page" : undefined}
              className={cn(
                 "lemma-assistant-conversation-row-button w-full border px-3 py-3 text-left text-sm transition-colors",
                 assistantRadius(radius, "item"),
                 isActive
                   ? "border-[color:var(--row-border)] bg-[var(--card-bg)] shadow-[var(--shadow-sm)]"
                   : "lemma-assistant-conversation-list-item-idle",
               )}
            >
              <div className="truncate font-medium">
                {renderConversationLabel
                  ? renderConversationLabel({ conversation, isActive })
                  : (conversation.title || "Untitled conversation")}
              </div>
              <div className="mt-1 flex items-center gap-1.5 text-xs text-[var(--text-secondary)]">
                <span className={cn("size-1.5 rounded-full flex-shrink-0", conversationStatusDotColor(conversation.status))} />
                <span>{relativeTimeAgo(conversation.updated_at || conversation.created_at)}</span>
              </div>
            </button>
          );
        })}
      </div>
    </aside>
  );
});

export interface AssistantPendingFileChipProps extends ComponentPropsWithoutRef<"div"> {
  label: ReactNode;
  onRemove?: () => void;
  radius?: LemmaAssistantRadius;
}

export interface AssistantComposerProps extends ComponentPropsWithoutRef<"div"> {
  floating?: ReactNode;
  status?: ReactNode;
  pendingFiles?: ReactNode;
  children: ReactNode;
  innerClassName?: string;
  tone?: AssistantSurfaceTone;
  radius?: LemmaAssistantRadius;
  compact?: boolean;
}

export const AssistantComposer = forwardRef<HTMLDivElement, AssistantComposerProps>(function AssistantComposer({
  floating,
  status,
  pendingFiles,
  children,
  innerClassName,
  tone = "subtle",
  radius = "lg",
  compact = false,
  className,
  ...props
}, ref) {
  return (
    <div
      ref={ref}
      data-tone={tone}
      data-has-status={status ? "true" : "false"}
      data-has-pending-files={pendingFiles ? "true" : "false"}
      data-has-floating={floating ? "true" : "false"}
      className={cn(
        "lemma-assistant-composer flex shrink-0 flex-col border-t border-transparent",
        compact ? "gap-1.5 px-3 py-2" : "gap-2 px-4 pb-3 pt-2 sm:px-6",
        tone === "default" && "bg-[var(--bg-canvas)]",
        tone === "subtle" && "bg-transparent",
        tone === "flat" && "border-transparent bg-transparent",
        assistantRadius(radius, "shell"),
        className,
      )}
      {...props}
    >
      {floating ? (
        <div className={cn("mx-auto flex w-full min-w-0 flex-wrap items-center gap-2", innerClassName)}>
          {floating}
        </div>
      ) : null}

      {status ? (
        <div className={cn("mx-auto min-h-6 w-full", innerClassName)} data-has-status="true">
          <div className="flex flex-wrap items-center gap-2">
            {status}
          </div>
        </div>
      ) : (
        <div className="min-h-0" data-has-status="false" />
      )}

      {pendingFiles ? (
        <div className={cn("mx-auto flex w-full flex-wrap gap-1.5", innerClassName)}>
          {pendingFiles}
        </div>
      ) : null}

      <div className={cn("mx-auto w-full min-w-0", innerClassName)}>{children}</div>
    </div>
  );
});

export const AssistantPendingFileChip = forwardRef<HTMLDivElement, AssistantPendingFileChipProps>(function AssistantPendingFileChip({
  label,
  onRemove,
  radius = "lg",
  className,
  ...props
}, ref) {
  return (
    <Badge
      ref={ref}
      variant="default"
      className={cn(
        "lemma-assistant-presented-file-badge inline-flex h-6 max-w-full items-center gap-1.5 px-2 text-xs",
        assistantRadius(radius, "inline"),
        className,
      )}
      {...props}
    >
      <span className="truncate">{label}</span>
      {onRemove ? (
        <button
          type="button"
          onClick={onRemove}
          className="lemma-assistant-file-remove-button inline-flex size-4 items-center justify-center rounded-sm text-[var(--text-secondary)] transition-colors hover:bg-[color:color-mix(in_srgb,var(--surface-2)_80%,transparent)] hover:text-[var(--text-primary)]"
          title="Remove file"
        >
          <X className="size-3" />
        </button>
      ) : null}
    </Badge>
  );
});

export interface AssistantStatusPillProps extends ComponentPropsWithoutRef<"div"> {
  label: ReactNode;
  subtle?: boolean;
  radius?: LemmaAssistantRadius;
}

export const AssistantStatusPill = forwardRef<HTMLDivElement, AssistantStatusPillProps>(function AssistantStatusPill({
  label,
  subtle = false,
  radius = "lg",
  className,
  ...props
}, ref) {
  void radius;

  return (
    <div
      ref={ref}
      className={cn(
        "inline-flex min-h-6 max-w-full items-center gap-2 px-1 py-0.5 text-sm text-[var(--text-secondary)] transition-colors",
        !subtle && "lemma-assistant-text-primary-soft",
        className,
      )}
      {...props}
    >
      <span className="relative flex size-2 shrink-0" aria-hidden="true">
        <span className="absolute inset-0 animate-ping rounded-full bg-[var(--action-primary)] opacity-30" />
        <span className="relative size-2 rounded-full bg-[var(--action-primary)]" />
      </span>
      <span className="truncate">{label}</span>
    </div>
  );
});
