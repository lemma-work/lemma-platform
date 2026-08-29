"use client";

import { useMemo } from "react";
import Link from "next/link";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import {
  isConversationRunningStatus,
  normalizeConversationStatus,
} from "lemma-sdk";
import { ArrowUpRight } from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import { formatAgentName } from "@/lib/utils/agents";
import { ResourceIdentity } from "@/components/shared/resource-identity";
import type { IdentityState } from "@/lib/identity/seeded-identity";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import { workspaceTabConversationQueryKey } from "@/lib/pods/workspace-tabs";
import {
  deriveSubagentActivities,
  mergeSubagentConversationSnapshots,
  readableSubagentTask,
  subagentActivitiesFor,
  subagentActivityPhase,
  type SubagentActivity,
  type SubagentActivityPhase,
  type SubagentConversationSnapshot,
} from "@/lib/assistant/subagent-activity";
import { currentPodIdFromBrowserPath } from "./assistant-resource-cards";
import type { AssistantMessagePart } from "lemma-sdk/react";

export type SubagentToolPart = Extract<AssistantMessagePart, { type: "tool" }>;

const CHILDREN_POLL_MS = 2000;
/** A ceiling, not a layout: the dock wraps, but a run that fans out to twenty
 *  agents should not push the composer down the screen. */
const MAX_VISIBLE_CHIPS = 6;

function snapshotRecords(value: unknown): SubagentConversationSnapshot[] {
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

/**
 * A conversation's sub-agents, named by the calls that spawned them and kept
 * current by the child conversations themselves.
 *
 * The children are the authority — the API lists them by `parent_id`, and a
 * status read there is true whether or not the parent ever awaited it. The
 * lifecycle calls only supply what a conversation row cannot: which agent was
 * asked, and what it was asked to do.
 *
 * Every caller shares one query key, so the dock's rail and a settled turn's
 * row cost one poll between them however many of them are mounted.
 */
export function useSubagentActivities({
  podId,
  parentConversationId,
  parts,
  isRunActive,
}: {
  podId?: string | null;
  parentConversationId: string | null;
  parts: SubagentToolPart[];
  isRunActive?: boolean;
}): SubagentActivity[] {
  const queryClient = useQueryClient();
  const seeds = useMemo(
    () => deriveSubagentActivities(parts.map((part) => part.toolInvocation)),
    [parts],
  );

  const childrenQuery = useQuery({
    queryKey: ["subagent-children", podId ?? null, parentConversationId],
    enabled: Boolean(podId && parentConversationId),
    refetchInterval: (query) => {
      const hasUnsettledChild = snapshotRecords(query.state.data).some((snapshot) => {
        const status = normalizeConversationStatus(snapshot.last_run_status ?? snapshot.status);
        return isConversationRunningStatus(status) || status === "WAITING";
      });
      return isRunActive || hasUnsettledChild ? CHILDREN_POLL_MS : false;
    },
    queryFn: async () => {
      const response = await getLemmaClient(podId as string).conversations.list({
        parent_id: parentConversationId,
        limit: 50,
      });
      // Opening a child is one click away, and the tab that opens cannot find
      // it in the pod's conversation list — it is a child, and that list holds
      // roots. Priming the cache here is what lets the tab arrive already
      // named instead of reading "Untitled conversation" until its own fetch
      // lands.
      if (podId) {
        const items = (response as { items?: unknown }).items;
        if (Array.isArray(items)) {
          items.forEach((item) => {
            const id = (item as { id?: unknown } | null)?.id;
            if (typeof id === "string") {
              queryClient.setQueryData(workspaceTabConversationQueryKey(podId, id), item);
            }
          });
        }
      }
      return response;
    },
  });

  const snapshots = useMemo(() => snapshotRecords(childrenQuery.data), [childrenQuery.data]);

  return useMemo(
    () => mergeSubagentConversationSnapshots(seeds, snapshots),
    [seeds, snapshots],
  );
}

/**
 * The face carries the state.
 *
 * A sub-agent is a being, and this app already draws beings: `ResourceIdentity`
 * gives every seed its own creature whose eyes, status pip and breathing are a
 * function of what it is doing. A working sub-agent that *looks* like it is
 * working needs no word saying so, which is the whole reason the chip has room
 * for the agent's name.
 */
const PHASE_STATE: Record<SubagentActivityPhase, IdentityState> = {
  working: 'running',
  waiting: 'waiting',
  failed: 'failed',
  stopped: 'asleep',
  complete: 'idle',
  unknown: 'idle',
};

/** Below this the pip stops drawing, and the pip is half the point. */
const FACE_SIZE = 20;

/**
 * Every state says what it is.
 *
 * The face alone was not enough. Two chips side by side — one still working,
 * one finished twenty minutes ago — were a green pip and a grey one at 20px,
 * which is a difference you can only see if you already know to look for it.
 * The word is small and muted where nothing is wrong, and coloured where a
 * person is wanted.
 */
function phaseStatus(phase: SubagentActivityPhase): { text: string; className: string } {
  if (phase === "working") {
    return { text: "Working", className: "text-[var(--action-primary)]" };
  }
  if (phase === "waiting") {
    return { text: "Needs you", className: "text-[var(--state-warning)]" };
  }
  if (phase === "failed") {
    return { text: "Failed", className: "text-[var(--state-error)]" };
  }
  if (phase === "stopped") {
    return { text: "Stopped", className: "text-[var(--text-tertiary)]" };
  }
  if (phase === "complete") {
    return { text: "Done", className: "text-[var(--text-tertiary)]" };
  }
  return { text: "Pending", className: "text-[var(--text-tertiary)]" };
}

function chipHref(podId: string, activity: SubagentActivity): string {
  const base = `/pod/${encodeURIComponent(podId)}/conversations/${encodeURIComponent(activity.conversationId as string)}`;
  const agentName = activity.agentName?.trim();
  // The child answers for its own agent when the spawn call is out of the
  // loaded window; naming it here only saves the route a lookup.
  return agentName ? `${base}?${new URLSearchParams({ agent: agentName }).toString()}` : base;
}

/**
 * One sub-agent, as a chip.
 *
 * Clicking it opens the child's own transcript in a workspace tab — a
 * sub-agent's conversation is a conversation, and the strip already knows how
 * to hold one and show whether it is still running. Nothing about it belongs
 * inlined under the parent's turn.
 */
function SubagentChip({
  activity,
  index,
  podId,
}: {
  activity: SubagentActivity;
  index: number;
  podId?: string | null;
}) {
  const phase = subagentActivityPhase(activity.status, activity.error);
  const status = phaseStatus(phase);
  const brief = readableSubagentTask(activity.task);
  // The agent's name is the label whenever there is one — it is short, stable
  // and the thing a reader is looking for. A brief only stands in for it.
  const label = activity.agentName
    ? formatAgentName(activity.agentName)
    : brief || `Sub-agent ${index + 1}`;
  // Two children of the same agent are two different beings doing two
  // different jobs, so the face is seeded per conversation rather than per
  // agent — otherwise a fan-out of five researchers is five identical faces.
  const faceSeed = activity.conversationId || activity.runId || `${activity.key}`;
  const openable = Boolean(podId && activity.conversationId);
  const title = [label, brief !== label ? brief : null, status.text]
    .filter(Boolean)
    .join(" · ");

  const body = (
    <>
      <ResourceIdentity
        seed={faceSeed}
        label={label}
        kind="being"
        state={PHASE_STATE[phase]}
        size={FACE_SIZE}
        className="shrink-0"
      />
      {/* The name never yields. It used to share the row with the raw spawn
          prompt, and flexbox squeezed "Worker researcher" down to a sliver so
          the chip read as a stray glyph and a task id — the two least useful
          things it could have shown. The prompt is in the tooltip now. */}
      <span className="min-w-0 truncate text-[var(--text-primary)]">{label}</span>
      <span className={cn("shrink-0", status.className)}>{status.text}</span>
      {/* Says where the click goes. A chip that opens a whole other transcript
          should not look like a chip that expands in place. */}
      {openable ? (
        <ArrowUpRight
          className="size-3 shrink-0 text-[var(--text-tertiary)]"
          strokeWidth={1.8}
          aria-hidden="true"
        />
      ) : null}
    </>
  );

  const className = cn(
    "inline-flex h-7 min-w-0 max-w-[18rem] items-center gap-1.5 rounded-full border py-0.5 pl-1 pr-2 text-xs",
    "border-[color:color-mix(in_srgb,var(--row-border)_72%,transparent)]",
    "bg-[color:color-mix(in_srgb,var(--bg-canvas)_96%,transparent)]",
  );

  if (!openable) {
    return <span className={className} title={title}>{body}</span>;
  }

  return (
    <Link
      href={chipHref(podId as string, activity)}
      title={`${title} · Opens in a tab`}
      className={cn(
        className,
        "custom-focus-ring transition-colors hover:bg-[color:color-mix(in_srgb,var(--surface-2)_45%,transparent)]",
      )}
    >
      {body}
    </Link>
  );
}

function ChipList({
  activities,
  podId,
  label,
  className,
}: {
  activities: SubagentActivity[];
  podId?: string | null;
  label: string;
  className?: string;
}) {
  const visible = activities.slice(0, MAX_VISIBLE_CHIPS);
  const overflow = activities.length - visible.length;

  return (
    <div
      className={cn("flex min-w-0 flex-wrap items-center gap-1.5", className)}
      role="list"
      aria-label={label}
    >
      {visible.map((activity, index) => (
        <span key={activity.key} role="listitem" className="min-w-0">
          <SubagentChip activity={activity} index={index} podId={podId} />
        </span>
      ))}
      {overflow > 0 ? (
        <span className="text-xs text-[var(--text-tertiary)]">+{overflow} more</span>
      ) : null}
    </div>
  );
}

/**
 * The sub-agents a turn delegated to, under the message that delegated.
 *
 * Working and finished sit in the same row, in spawn order. They were split for
 * a while — live ones docked above the composer so they could not scroll away,
 * finished ones left behind in the turn — but the dock put a chip a long way
 * from the sentence that explained it, and reading the pair as one row is worth
 * more than keeping the live half permanently on screen.
 *
 * Anchored to the spawn rather than to the finish: which turn a sub-agent
 * happened to complete during is an artifact of timing, while the turn that
 * asked for it is the record of the decision.
 */
export function AssistantSubagentChipRow({
  parts,
  parentConversationId,
  isRunActive,
}: {
  parts: SubagentToolPart[];
  parentConversationId: string | null;
  isRunActive?: boolean;
}) {
  const podId = currentPodIdFromBrowserPath();
  const activities = useSubagentActivities({ podId, parentConversationId, parts, isRunActive });
  const seeds = useMemo(
    () => deriveSubagentActivities(parts.map((part) => part.toolInvocation)),
    [parts],
  );
  const owned = subagentActivitiesFor(activities, seeds);

  if (owned.length === 0) return null;

  return <ChipList activities={owned} podId={podId} label="Sub-agents" className="mt-1.5" />;
}
