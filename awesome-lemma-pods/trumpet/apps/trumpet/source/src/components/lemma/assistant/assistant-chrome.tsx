"use client";

import { forwardRef, type ComponentPropsWithoutRef, type ReactNode } from "react";
import { X } from "lucide-react";
import { cn } from "@/components/lemma/lib/utils";
import { Button } from "@/components/lemma/ui/button";
import { Badge } from "@/components/lemma/ui/badge";
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from "@/components/lemma/ui/select";
import type {
  AssistantConversationListItem,
  AssistantConversationRenderArgs,
  LemmaAssistantRadius,
} from "./assistant-types.js";

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
  if (s === "running" || s === "active") return "bg-primary";
  if (s === "completed" || s === "done") return "bg-green-500";
  if (s === "error" || s === "failed") return "bg-destructive";
  return "bg-amber-500";
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
  controls?: ReactNode;
  tone?: AssistantSurfaceTone;
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
      className={cn("min-h-0 flex-1 overflow-y-auto bg-background px-4 py-6 [overflow-anchor:none] sm:px-6 lg:px-8", className)}
      {...props}
    >
      <div className={cn("mx-auto flex w-full max-w-3xl flex-col gap-8", innerClassName)}>
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
        <div className={cn("min-h-0 overflow-hidden border border-border/60 bg-muted/25 shadow-sm", assistantRadius(radius, "shell"))}>
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
  controls,
  tone = "subtle",
  className,
  ...props
}, ref) {
  return (
    <div
      ref={ref}
      data-tone={tone}
      className={cn(
        "flex shrink-0 items-center justify-between gap-3 border-b border-border/60 px-4 py-3 sm:px-6",
        tone === "default" && "bg-card/95",
        tone === "subtle" && "bg-background/95",
        tone === "flat" && "bg-transparent",
        className,
      )}
      {...props}
    >
      <div className="flex min-w-0 items-center gap-3">
        {badge ? (
          <span className="flex size-9 shrink-0 items-center justify-center rounded-lg bg-primary text-primary-foreground">
            {badge}
          </span>
        ) : null}
        <div className="min-w-0">
          <h3 className="truncate text-lg font-semibold tracking-tight text-foreground">{title}</h3>
          {subtitle ? (
            <p className="truncate text-sm text-muted-foreground">{subtitle}</p>
          ) : null}
        </div>
      </div>
      {controls ? (
        <div className="flex shrink-0 items-center gap-2">{controls}</div>
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
    <aside ref={ref} className={cn("flex h-full min-h-0 flex-col overflow-hidden border border-border/60 bg-muted/25", assistantRadius(radius, "shell"), className)} {...props}>
      <div className="border-b border-border/60 px-4 py-3">
        <div className="flex items-center justify-between gap-3">
          <div className="min-w-0">
            <div className="truncate text-sm font-semibold text-foreground">{title}</div>
            <div className="mt-0.5 text-xs text-muted-foreground">
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
              aria-selected={isActive}
              className={cn(
                 "w-full border px-3 py-3 text-left text-sm transition-colors",
                 assistantRadius(radius, "item"),
                 isActive
                   ? "border-border bg-background shadow-sm"
                   : "border-transparent bg-transparent text-foreground/80 hover:border-border/50 hover:bg-background/70",
               )}
            >
              <div className="truncate font-medium">
                {renderConversationLabel
                  ? renderConversationLabel({ conversation, isActive })
                  : (conversation.title || "Untitled conversation")}
              </div>
              <div className="mt-1 flex items-center gap-1.5 text-xs text-muted-foreground">
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

export interface AssistantModelPickerProps<TValue extends string = string> extends Omit<ComponentPropsWithoutRef<"div">, "onChange"> {
  value: TValue | null;
  options: TValue[];
  disabled?: boolean;
  autoLabel?: ReactNode;
  getOptionLabel?: (value: TValue) => ReactNode;
  onChange: (value: TValue | null) => void;
}

export const AssistantModelPicker = forwardRef(function AssistantModelPicker<TValue extends string = string>({
  value,
  options,
  disabled,
  autoLabel = "Auto",
  getOptionLabel,
  onChange,
  className,
  ...props
}: AssistantModelPickerProps<TValue>, ref: React.ForwardedRef<HTMLDivElement>) {
  const autoValue = "__AUTO__";

  return (
    <div ref={ref} className={className} {...props}>
      <Select
        value={value ?? autoValue}
        onValueChange={(val) => onChange(val === autoValue ? null : (val as TValue))}
        disabled={disabled}
      >
        <SelectTrigger
          className="inline-flex h-9 min-w-28 items-center gap-2 rounded-lg border border-border/80 bg-background px-2.5 text-sm font-medium shadow-none hover:border-primary/40"
          aria-label="Conversation model"
        >
          <span className="rounded-full border border-border/60 bg-muted/60 px-1.5 py-0.5 text-[11px] font-semibold text-muted-foreground">Model</span>
          <SelectValue className="min-w-0 text-sm font-semibold" />
        </SelectTrigger>
        <SelectContent align="end">
          <SelectItem value={autoValue}>{autoLabel}</SelectItem>
          {options.map((option) => (
            <SelectItem key={option} value={option}>
              {getOptionLabel ? getOptionLabel(option) : option}
            </SelectItem>
          ))}
        </SelectContent>
      </Select>
    </div>
  );
}) as <TValue extends string = string>(
  props: AssistantModelPickerProps<TValue> & React.RefAttributes<HTMLDivElement>
) => React.ReactElement | null;

export interface AssistantAskOverlayProps extends ComponentPropsWithoutRef<"div"> {
  questionNumber: number;
  totalQuestions: number;
  question: ReactNode;
  options: string[];
  selectedOptions: string[];
  canContinue: boolean;
  continueLabel: ReactNode;
  onSelectOption: (option: string) => void;
  onContinue?: () => void;
  onSkip?: () => void;
  mode?: "single_select" | "multi_select" | "rank_priorities";
  radius?: LemmaAssistantRadius;
}

export const AssistantAskOverlay = forwardRef<HTMLDivElement, AssistantAskOverlayProps>(function AssistantAskOverlay({
  questionNumber,
  totalQuestions,
  question,
  options,
  selectedOptions,
  canContinue,
  continueLabel,
  onSelectOption,
  onContinue,
  onSkip,
  mode = "single_select",
  radius = "lg",
  className,
  ...props
}, ref) {
  return (
    <div ref={ref} className={cn("flex flex-col gap-3 border border-border/70 bg-background/95 p-4 shadow-sm", assistantRadius(radius, "shell"), className)} {...props}>
      <div className="flex items-start justify-between gap-3">
        <div className="min-w-0">
          <Badge variant="outline" className="w-fit text-xs">
            Question {questionNumber} of {totalQuestions}
          </Badge>
          <p className="mt-1.5 text-sm font-medium text-foreground">
            {question}
          </p>
        </div>
        {onSkip ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={onSkip}
            className="shrink-0 text-xs text-muted-foreground"
          >
            Skip
          </Button>
        ) : null}
      </div>

      <div className="flex max-h-64 flex-col gap-2 overflow-y-auto">
        {options.map((option, optionIndex) => {
          const isSelected = selectedOptions.includes(option);
          const rankLabel = mode === "rank_priorities" && isSelected
            ? selectedOptions.indexOf(option) + 1
            : null;

          return (
            <Button
              key={`${option}-${optionIndex}`}
              type="button"
              variant={isSelected ? "secondary" : "outline"}
              size="sm"
              onClick={() => onSelectOption(option)}
              className={cn(
                "h-auto justify-start whitespace-normal py-2 text-left text-sm",
                assistantRadius(radius, "item"),
                isSelected && "border-primary/40 bg-primary/10",
              )}
            >
              <span className="flex items-center gap-2">
                {rankLabel ? (
                  <Badge variant="default" className="flex size-5 items-center justify-center p-0 text-xs">
                    {rankLabel}
                  </Badge>
                ) : (
                  <span
                    className={cn(
                      "size-4 shrink-0 rounded-full border-2",
                      isSelected ? "border-primary bg-primary/20" : "border-muted-foreground/30",
                    )}
                  />
                )}
                {option}
              </span>
            </Button>
          );
        })}
      </div>

      {onContinue ? (
        <div className="flex justify-end pt-1">
          <Button
            type="button"
            variant={canContinue ? "default" : "outline"}
            size="sm"
            onClick={onContinue}
            disabled={!canContinue}
            className="text-xs"
          >
            {continueLabel}
          </Button>
        </div>
      ) : null}
    </div>
  );
});

export interface AssistantPendingFileChipProps extends ComponentPropsWithoutRef<"span"> {
  label: ReactNode;
  onRemove?: () => void;
  radius?: LemmaAssistantRadius;
}

export interface AssistantComposerProps extends ComponentPropsWithoutRef<"div"> {
  floating?: ReactNode;
  status?: ReactNode;
  pendingFiles?: ReactNode;
  children: ReactNode;
  tone?: AssistantSurfaceTone;
  radius?: LemmaAssistantRadius;
}

export const AssistantComposer = forwardRef<HTMLDivElement, AssistantComposerProps>(function AssistantComposer({
  floating,
  status,
  pendingFiles,
  children,
  tone = "subtle",
  radius = "lg",
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
        "flex shrink-0 flex-col gap-2 border-t border-border/60 px-4 py-3 sm:px-6",
        tone === "default" && "bg-background",
        tone === "subtle" && "bg-muted/25",
        tone === "flat" && "border-transparent bg-transparent",
        assistantRadius(radius, "shell"),
        className,
      )}
      {...props}
    >
      {floating ? (
        <div className="flex flex-wrap items-center gap-2">
          {floating}
        </div>
      ) : null}

      {status ? (
        <div className="min-h-6" data-has-status="true">
          <div className="flex flex-wrap items-center gap-2">
            {status}
          </div>
        </div>
      ) : (
        <div className="min-h-0" data-has-status="false" />
      )}

      {pendingFiles ? (
        <div className="flex flex-wrap gap-1.5">
          {pendingFiles}
        </div>
      ) : null}

      <div className="min-w-0">{children}</div>
    </div>
  );
});

export const AssistantPendingFileChip = forwardRef<HTMLSpanElement, AssistantPendingFileChipProps>(function AssistantPendingFileChip({
  label,
  onRemove,
  radius = "lg",
  className,
  ...props
}, ref) {
  return (
    <Badge
      ref={ref as React.Ref<HTMLSpanElement>}
      variant="secondary"
      className={cn(
        "inline-flex h-6 max-w-full items-center gap-1.5 px-2 text-xs",
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
          className="inline-flex size-4 items-center justify-center rounded-sm text-muted-foreground transition-colors hover:bg-muted/80 hover:text-foreground"
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
  return (
    <div
      ref={ref}
      className={cn(
        "inline-flex min-h-8 max-w-full items-center gap-2 border px-3 py-1.5 text-sm transition-colors",
        assistantRadius(radius, "inline"),
        subtle
          ? "border-border/70 bg-background text-muted-foreground"
          : "border-primary/25 bg-primary/10 text-foreground/80",
        className,
      )}
      {...props}
    >
      <span className="relative flex size-2.5 shrink-0">
        {subtle ? null : <span className="absolute inset-0 animate-ping rounded-full bg-primary/45" />}
        <span className="relative size-2.5 rounded-full bg-primary" />
      </span>
      <span className="truncate">{label}</span>
    </div>
  );
});
