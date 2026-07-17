"use client";

import { useMemo, useState } from "react";
import Link from "next/link";
import { useSearchParams } from "next/navigation";
import { useQuery } from "@tanstack/react-query";
import {
  isConversationRunningStatus,
  latestAssistantText,
  normalizeConversationStatus,
} from "lemma-sdk";
import {
  AlertCircle,
  Bot,
  CheckCircle2,
  ChevronDown,
  Circle,
  ExternalLink,
  PauseCircle,
  Square,
} from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import { formatAgentName } from "@/lib/utils/agents";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import {
  deriveSubagentActivities,
  mergeSubagentConversationSnapshots,
  subagentActivityPhase,
  summarizeSubagentActivities,
  type SubagentActivity,
  type SubagentActivityPhase,
  type SubagentConversationSnapshot,
} from "@/lib/assistant/subagent-activity";
import { currentPodIdFromBrowserPath } from "./assistant-resource-cards";
import type { AssistantMessagePart } from "lemma-sdk/react";

type SubagentToolPart = Extract<AssistantMessagePart, { type: "tool" }>;

function snapshotsFromResponse(value: unknown): SubagentConversationSnapshot[] {
  const items = value && typeof value === "object" && !Array.isArray(value)
    ? (value as { items?: unknown }).items
    : undefined;
  if (!Array.isArray(items)) return [];

  return items.flatMap((item) => {
    if (!item || typeof item !== "object" || Array.isArray(item)) return [];
    const record = item as Record<string, unknown>;
    if (typeof record.id !== "string") return [];
    return [{
      id: record.id,
      status: typeof record.status === "string" ? record.status : null,
      last_run_status: typeof record.last_run_status === "string" ? record.last_run_status : null,
      title: typeof record.title === "string" ? record.title : null,
      output: record.output,
      last_run_error: typeof record.last_run_error === "string" ? record.last_run_error : null,
    }];
  });
}

function phasePresentation(phase: SubagentActivityPhase): {
  label: string;
  className: string;
  icon: React.ReactNode;
} {
  if (phase === "working") {
    return {
      label: "Working",
      className: "text-[var(--action-primary)]",
      icon: <Circle className="size-3 fill-current animate-pulse" />,
    };
  }
  if (phase === "waiting") {
    return {
      label: "Waiting",
      className: "text-[var(--state-warning)]",
      icon: <PauseCircle className="size-3.5" />,
    };
  }
  if (phase === "failed") {
    return {
      label: "Failed",
      className: "text-[var(--state-error)]",
      icon: <AlertCircle className="size-3.5" />,
    };
  }
  if (phase === "stopped") {
    return {
      label: "Stopped",
      className: "text-[var(--text-tertiary)]",
      icon: <Square className="size-3 fill-current" />,
    };
  }
  if (phase === "complete") {
    return {
      label: "Complete",
      className: "text-[var(--state-success)]",
      icon: <CheckCircle2 className="size-3.5" />,
    };
  }
  return {
    label: "Pending",
    className: "text-[var(--text-tertiary)]",
    icon: <Circle className="size-3" />,
  };
}

function truncateOutput(value: string, max = 1600): string {
  return value.length > max ? `${value.slice(0, max).trimEnd()}…` : value;
}

function childConversationHref(
  podId: string,
  conversationId: string,
  agentName?: string | null,
): string {
  const base = `/pod/${encodeURIComponent(podId)}/conversations/${encodeURIComponent(conversationId)}`;
  const normalizedAgentName = agentName?.trim();
  if (!normalizedAgentName) return base;
  return `${base}?${new URLSearchParams({ agent: normalizedAgentName }).toString()}`;
}

function SubagentActivityDetails({
  activity,
  podId,
  parentAgentName,
}: {
  activity: SubagentActivity;
  podId?: string | null;
  parentAgentName?: string | null;
}) {
  const phase = subagentActivityPhase(activity.status, activity.error);
  const conversationId = activity.conversationId;
  const shouldPoll = phase === "working" || phase === "waiting";
  const messagesQuery = useQuery({
    queryKey: ["subagent-activity-messages", podId ?? null, conversationId ?? null],
    enabled: Boolean(podId && conversationId),
    refetchInterval: shouldPoll ? 2000 : false,
    queryFn: () => getLemmaClient(podId as string).conversations.messages.list(
      conversationId as string,
      { limit: 40 },
    ),
  });
  const messageItems = messagesQuery.data && Array.isArray(messagesQuery.data.items)
    ? messagesQuery.data.items
    : [];
  const latest = latestAssistantText(
    messageItems as Parameters<typeof latestAssistantText>[0],
  );
  const detail = activity.error || activity.output || latest;
  const resolvedAgentName = activity.agentName || parentAgentName;

  return (
    <div className="border-t border-[color:color-mix(in_srgb,var(--row-border)_45%,transparent)] px-3 pb-3 pt-2.5">
      {detail ? (
        <p className={cn(
          "max-h-48 overflow-y-auto whitespace-pre-wrap break-words text-sm leading-6",
          activity.error ? "text-[var(--state-error)]" : "text-[var(--text-secondary)]",
        )}>
          {truncateOutput(detail)}
        </p>
      ) : shouldPoll || messagesQuery.isLoading ? (
        <p className="text-xs text-[var(--text-tertiary)]">Working in its child conversation…</p>
      ) : (
        <p className="text-xs text-[var(--text-tertiary)]">No child output was recorded.</p>
      )}
      {podId && conversationId ? (
        <Link
          href={childConversationHref(podId, conversationId, resolvedAgentName)}
          className="mt-2 inline-flex items-center gap-1.5 text-xs font-medium text-[var(--action-primary)] hover:underline"
        >
          Open child conversation
          <ExternalLink className="size-3" aria-hidden="true" />
        </Link>
      ) : null}
    </div>
  );
}

export function SubagentActivityRollup({
  parts,
  parentConversationId,
  isRunActive,
}: {
  parts: SubagentToolPart[];
  parentConversationId: string | null;
  isRunActive?: boolean;
}) {
  const [isExpanded, setIsExpanded] = useState(false);
  const [expandedChildKey, setExpandedChildKey] = useState<string | null>(null);
  const searchParams = useSearchParams();
  const parentAgentName = searchParams.get("agent");
  const podId = currentPodIdFromBrowserPath();
  const derivedActivities = useMemo(
    () => deriveSubagentActivities(parts.map((part) => part.toolInvocation)),
    [parts],
  );
  const childrenQuery = useQuery({
    queryKey: ["subagent-orchestration", podId ?? null, parentConversationId],
    enabled: Boolean(podId && parentConversationId),
    refetchInterval: (query) => {
      const snapshots = snapshotsFromResponse(query.state.data);
      const hasActiveChild = snapshots.some((snapshot) => {
        const status = normalizeConversationStatus(snapshot.last_run_status ?? snapshot.status);
        return isConversationRunningStatus(status) || status === "WAITING";
      });
      return isRunActive || hasActiveChild ? 2000 : false;
    },
    queryFn: () => getLemmaClient(podId as string).conversations.list({
      parent_id: parentConversationId,
      limit: 50,
    }),
  });
  const snapshots = useMemo(
    () => snapshotsFromResponse(childrenQuery.data),
    [childrenQuery.data],
  );
  const activities = useMemo(
    () => mergeSubagentConversationSnapshots(derivedActivities, snapshots),
    [derivedActivities, snapshots],
  );
  const summary = summarizeSubagentActivities(activities);
  const hasWorkingChild = activities.some((activity) => {
    const phase = subagentActivityPhase(activity.status, activity.error);
    return phase === "working" || phase === "waiting";
  });

  if (parts.length === 0 || activities.length === 0) return null;

  return (
    <div className="overflow-hidden rounded-lg border border-[color:color-mix(in_srgb,var(--row-border)_72%,transparent)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_96%,transparent)]">
      <button
        type="button"
        onClick={() => setIsExpanded((previous) => !previous)}
        className="lemma-assistant-tool-rollup-toggle-button flex w-full items-center gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-[color:color-mix(in_srgb,var(--surface-2)_45%,transparent)]"
        aria-expanded={isExpanded}
      >
        <Bot className="size-4 shrink-0 text-[var(--text-tertiary)]" aria-hidden="true" />
        <span className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--text-primary)]">
          {summary}
        </span>
        {hasWorkingChild ? (
          <span className="size-1.5 shrink-0 rounded-full bg-[var(--action-primary)] animate-pulse" aria-label="Sub-agents working" />
        ) : null}
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-[var(--text-tertiary)] transition-transform",
            !isExpanded && "-rotate-90",
          )}
          aria-hidden="true"
        />
      </button>

      {isExpanded ? (
        <div className="divide-y divide-[color:color-mix(in_srgb,var(--row-border)_45%,transparent)] border-t border-[color:color-mix(in_srgb,var(--row-border)_45%,transparent)]">
          {activities.map((activity, index) => {
            const phase = subagentActivityPhase(activity.status, activity.error);
            const presentation = phasePresentation(phase);
            const isChildExpanded = expandedChildKey === activity.key;
            const title = activity.task || (activity.agentName
              ? formatAgentName(activity.agentName)
              : `Sub-agent ${index + 1}`);

            return (
              <div key={activity.key}>
                <button
                  type="button"
                  onClick={() => setExpandedChildKey((previous) => (
                    previous === activity.key ? null : activity.key
                  ))}
                  className="lemma-assistant-tool-group-button flex w-full items-start gap-2.5 px-3 py-2.5 text-left transition-colors hover:bg-[color:color-mix(in_srgb,var(--surface-2)_38%,transparent)]"
                  aria-expanded={isChildExpanded}
                >
                  <span className={cn("mt-0.5 shrink-0", presentation.className)}>
                    {presentation.icon}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span className="line-clamp-2 text-sm leading-5 text-[var(--text-primary)]">{title}</span>
                    {activity.agentName && activity.task ? (
                      <span className="mt-0.5 block text-xs text-[var(--text-tertiary)]">
                        {formatAgentName(activity.agentName)}
                      </span>
                    ) : null}
                  </span>
                  <span className={cn("shrink-0 text-xs leading-5", presentation.className)}>
                    {presentation.label}
                  </span>
                </button>
                {isChildExpanded ? (
                  <SubagentActivityDetails
                    activity={activity}
                    podId={podId}
                    parentAgentName={parentAgentName}
                  />
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
    </div>
  );
}
