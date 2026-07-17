"use client";

// Tool-detail rendering extracted from assistant-message-group.tsx: the expandable
// tool-details panel, the inline tool call, the activity-summary helpers, the
// tool-activity rollup, and the copy/timestamp text wrapper. RunTraceHeader lives
// here too (it is re-exported from assistant-message-group to preserve its API)
// so the rollup can use it without a runtime import cycle.

import { useState, type ReactNode } from "react";
import {
  formatDurationCompact,
  isAskUserToolName,
  isLongRunningToolResult,
  isToolInvocationActive,
  isUserInteractionToolName,
  isRenderableUserInteractionInvocation,
  userApprovalResolvedDecision,
} from "lemma-sdk";
import { Check, ChevronDown, Copy } from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import { Button } from "@/components/ui/button";
import {
  buildDisplayResourceHref,
  extractDisplayResourceFromInvocation,
} from "@/lib/assistant/display-resource";
import {
  commentLabelFromArgs,
  countSummarizablePayloadEntries,
  formatToolDisplayName,
  humanizeKey,
  pickPreferredEntries,
  summarizeToolPayload,
  toolCallPrimaryLabel,
} from "./assistant-format";
import { DetailsWithCopy, contextualToolDetails } from "./assistant-tool-cards";
import { ReasoningPartCard } from "./assistant-parts";
import { SubagentActivityRollup } from "./assistant-subagent-activity";
import { isSubagentLifecycleToolName } from "@/lib/assistant/subagent-activity";
import {
  currentPodIdFromBrowserPath,
  isCurrentBrowserHref,
} from "./assistant-resource-cards";
import {
  AskUserCard,
  InlineUserApprovalCall,
  UserApprovalCard,
} from "./assistant-approval-cards";
import type {
  AssistantMessagePart,
  AssistantRenderableMessage,
  AssistantToolInvocation,
} from "lemma-sdk/react";
import type { AssistantToolRenderArgs } from "./assistant-types";
import type {
  ToolCardArgs,
  ToolCardResult,
  UserApprovalDecision,
} from "./assistant-experience";
import { ToolCallIcon } from "./assistant-tool-icon";

export function ToolDetailsPanel({
  toolCallId,
  toolName,
  args,
  state,
  result,
  onNavigateResource,
  onResolveUserApproval,
  renderToolInvocation,
  message,
  activeConversationId,
}: {
  toolCallId: string;
  toolName: string;
  args: ToolCardArgs;
  state: string;
  result?: ToolCardResult;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
  message: AssistantRenderableMessage;
  activeConversationId: string | null;
}) {
	  const resultData = result || {};
	  const primaryLabel = toolCallPrimaryLabel(toolName, args);
	  const hasCommentLabel = !!commentLabelFromArgs(args);
	  const toolDisplayName = formatToolDisplayName(toolName);
	  const displayResource = extractDisplayResourceFromInvocation({
	    toolCallId,
	    toolName,
	    args,
	  });
	  const displayResourcePodId = currentPodIdFromBrowserPath();
	  const displayResourceHref = displayResource && displayResourcePodId
	    ? buildDisplayResourceHref({
	      podId: displayResourcePodId,
	      request: displayResource.request,
	      conversationId: activeConversationId,
	      toolCallId: displayResource.toolCallId,
	    })
	    : null;
	  const isOnDisplayResourceHref = isCurrentBrowserHref(displayResourceHref);
	  const canOpenDisplayResource =
	    !!displayResource
	    && !!displayResourceHref
	    && state === "result"
	    && resultData.success !== false
	    && !!onNavigateResource;
	  const canNavigate =
	    state === "result"
	    && resultData.success !== false
    && typeof resultData.resourceType === "string"
    && typeof resultData.resourceId === "string";
  const summaryOptions = {
    input: { excludeKeys: ["comment", "request", "wait_config"] },
    output: { excludeKeys: ["success", "completed"] },
  };
  const inputEntries = summarizeToolPayload(args, summaryOptions.input);
  const outputEntries = summarizeToolPayload(resultData, summaryOptions.output);
  const inputHighlights = pickPreferredEntries(inputEntries, [
    "cmd",
    "query",
    "path",
    "filepath",
    "filepaths",
    "table",
    "resource_type",
    "resourceType",
    "resource_id",
    "resourceId",
  ], 3);
  const outputHighlights = pickPreferredEntries(outputEntries, [
    "message",
    "stdout",
    "stderr",
    "error",
    "resource_type",
    "resourceType",
    "resource_id",
    "resourceId",
    "exit_code",
    "session_id",
  ], 3);
  const detailRows = [
    {
      label: "Tool",
      value: hasCommentLabel ? `${toolDisplayName} (${toolName})` : toolDisplayName,
    },
    ...inputHighlights.map((entry) => ({
      label: humanizeKey(entry.key),
      value: entry.value,
    })),
    ...outputHighlights.map((entry) => ({
      label: humanizeKey(entry.key),
      value: entry.value,
    })),
  ].slice(0, 8);
  const hiddenInputCount = Math.max(0, countSummarizablePayloadEntries(args, summaryOptions.input) - inputEntries.length);
  const hiddenOutputCount = Math.max(0, countSummarizablePayloadEntries(resultData, summaryOptions.output) - outputEntries.length);

  const interactionInvocation = {
      toolCallId,
      toolName,
      args,
      state: (state === "result" ? "result" : "call") as "result" | "call",
      ...(result ? { result } : {}),
    };
  if (isRenderableUserInteractionInvocation(interactionInvocation)) {
    return (
      <div className="mt-1.5">
        {isAskUserToolName(toolName) ? (
          <AskUserCard
            invocation={interactionInvocation}
            onResolveUserApproval={onResolveUserApproval}
          />
        ) : (
          <UserApprovalCard
            invocation={interactionInvocation}
            onResolveUserApproval={onResolveUserApproval}
          />
        )}
      </div>
    );
  }

  if (renderToolInvocation) {
    return (
      <div className="mt-1.5 rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_86%,transparent)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_96%,transparent)] p-3.5">
        {renderToolInvocation({
          invocation: {
            toolCallId: "detail-tool",
            toolName,
            args,
            state: state === "result" ? "result" : "call",
            ...(result ? { result } : {}),
          },
          message,
          activeConversationId,
        })}
      </div>
    );
  }

  const contextualDetails = contextualToolDetails({
    toolName,
    args,
    state,
    result: resultData,
  });

  if (contextualDetails) {
    return contextualDetails;
  }

  return (
    <div className="mt-1.5 rounded-md border border-[color:color-mix(in_srgb,var(--row-border)_86%,transparent)] bg-[color:color-mix(in_srgb,var(--bg-canvas)_96%,transparent)] p-3.5">
      <div className="mb-2 flex items-start justify-between gap-2">
        <div>
          <div className="text-sm text-[var(--text-primary)]">{primaryLabel}</div>
          {hasCommentLabel ? (
            <div className="text-xs text-[var(--text-secondary)]">{toolDisplayName}</div>
          ) : null}
        </div>
	        {canOpenDisplayResource ? (
	            <Button
	              type="button"
	              variant="outline"
	              size="sm"
	              onClick={() => onNavigateResource?.("display_resource", displayResource.toolCallId, {
	                request: displayResource.request,
	                conversationId: activeConversationId,
	              })}
	              disabled={isOnDisplayResourceHref}
	              className="h-7 rounded-full px-2.5 text-xs"
	            >
	              {isOnDisplayResourceHref ? "Here" : "Open view"}
	            </Button>
	        ) : canNavigate && onNavigateResource ? (
	            <Button
	              type="button"
	              variant="link"
              size="sm"
	              onClick={() => onNavigateResource?.(resultData.resourceType as string, resultData.resourceId as string, {
	                conversationId: activeConversationId,
	              })}
              className="text-xs"
            >
              Open
            </Button>
        ) : null}
      </div>
      <div>
        <div>
          <dl className="mt-1.5 flex flex-col gap-1.5">
            {detailRows.map((row, index) => (
              <div key={`${row.label}-${index}`} className="grid grid-cols-[minmax(96px,auto)_minmax(0,1fr)] items-start gap-2">
                <dt className="text-xs font-semibold text-[var(--text-secondary)]">{row.label}</dt>
                <dd className="break-words text-sm leading-relaxed text-[var(--text-primary)]">{row.value}</dd>
              </div>
            ))}
          </dl>
          {hiddenInputCount > 0 ? (
            <div className="mt-1 text-xs text-[var(--text-secondary)]">+{hiddenInputCount} more input field{hiddenInputCount === 1 ? "" : "s"}</div>
          ) : null}
          {hiddenOutputCount > 0 ? (
            <div className="mt-1 text-xs text-[var(--text-secondary)]">+{hiddenOutputCount} more output field{hiddenOutputCount === 1 ? "" : "s"}</div>
          ) : null}
          <div className="mt-1.5 flex flex-col gap-1">
            <DetailsWithCopy label="Raw input JSON" value={JSON.stringify(args, null, 2)} />
            {Object.keys(resultData).length > 0 ? (
              <DetailsWithCopy label="Raw output JSON" value={JSON.stringify(resultData, null, 2)} />
            ) : null}
          </div>
        </div>
      </div>
    </div>
  );
}

function InlineToolCall({
  invocation,
  isSelected,
  onClick,
}: {
  invocation: AssistantToolInvocation;
  isSelected: boolean;
  onClick: () => void;
}) {
  const resultData = (invocation.result || {}) as ToolCardResult;
  const isExecuting = isToolInvocationActive(invocation);
  const isFailed = !isExecuting && invocation.state === "result" && resultData.success === false;
  const primaryLabel = toolCallPrimaryLabel(invocation.toolName, invocation.args);

  return (
    <button
      type="button"
      onClick={onClick}
      className="lemma-assistant-inline-tool-button inline-flex max-w-full items-center gap-2 border-0 bg-transparent p-0 text-left transition-colors"
      data-state={isExecuting ? "executing" : isFailed ? "failed" : "complete"}
      data-selected={isSelected}
    >
      <ToolCallIcon
        toolName={invocation.toolName}
        className="size-3.5 shrink-0 text-current opacity-80"
      />
      <span
        className={cn(
          "min-w-0 truncate",
          isExecuting && "lemma-assistant-thinking-shimmer bg-clip-text text-transparent animate-[lemma-skeleton-breathe_1.5s_ease-in-out_infinite]",
        )}
      >
        {primaryLabel}
      </span>
      {isFailed ? (
        <span
          className="shrink-0 text-xs font-medium leading-none text-[var(--state-error)]"
          aria-label="Tool failed"
          title="Tool failed"
        >
          !
        </span>
      ) : null}
    </button>
  );
}

export function pluralize(count: number, singular: string, plural = `${singular}s`): string {
  return `${count} ${count === 1 ? singular : plural}`;
}

function joinActivityPhrases(phrases: string[]): string {
  if (phrases.length <= 1) return phrases[0] ?? "";
  if (phrases.length === 2) return `${phrases[0]} and ${phrases[1]}`;
  return `${phrases.slice(0, -1).join(", ")} and ${phrases[phrases.length - 1]}`;
}

/** A human "did x, y and z" line for a block of tool calls — tallied by the
 * tool's display name (so repeats collapse to "×N"), preserving call order,
 * and capped so a long block reads "a, b, c, d +K more" instead of a wall. */
function formatToolActivitySummary(toolParts: Array<Extract<AssistantMessagePart, { type: "tool" }>>): string | null {
  if (toolParts.length === 0) return null;

  const order: string[] = [];
  const counts = new Map<string, number>();
  for (const part of toolParts) {
    const name = formatToolDisplayName(part.toolInvocation.toolName);
    if (!counts.has(name)) {
      counts.set(name, 0);
      order.push(name);
    }
    counts.set(name, (counts.get(name) ?? 0) + 1);
  }

  const phraseFor = (name: string) => {
    const count = counts.get(name) ?? 1;
    return count > 1 ? `${name} ×${count}` : name;
  };

  const MAX_NAMED = 4;
  if (order.length <= MAX_NAMED) {
    return joinActivityPhrases(order.map(phraseFor));
  }
  const shown = order.slice(0, MAX_NAMED).map(phraseFor);
  return `${shown.join(", ")} +${order.length - MAX_NAMED} more`;
}

type ToolActivityPart = Extract<AssistantMessagePart, { type: "tool" }>;
type ReasoningActivityPart = Extract<AssistantMessagePart, { type: "reasoning" }>;
type ActivityDetailBlock =
  | { id: string; type: "reasoning"; part: ReasoningActivityPart }
  | { id: string; type: "tool-group"; parts: ToolActivityPart[] };

function groupActivityDetailParts(detailParts: Array<ToolActivityPart | ReasoningActivityPart>): ActivityDetailBlock[] {
  const blocks: ActivityDetailBlock[] = [];

  detailParts.forEach((part) => {
    if (part.type === "reasoning") {
      blocks.push({
        id: part.id,
        type: "reasoning",
        part,
      });
      return;
    }

    const previousBlock = blocks[blocks.length - 1];
    if (previousBlock?.type === "tool-group") {
      previousBlock.parts.push(part);
      return;
    }

    blocks.push({
      id: `${part.id}-group`,
      type: "tool-group",
      parts: [part],
    });
  });

  return blocks;
}

export function RunTraceHeader({
  label,
  isExpanded,
  isInteractive,
  onToggle,
}: {
  label: string;
  isExpanded?: boolean;
  isInteractive?: boolean;
  onToggle?: () => void;
}) {
  const content = (
    <>
      <span className="min-w-0 truncate">{label}</span>
      {isInteractive ? (
        <ChevronDown
          className={cn(
            "size-4 shrink-0 text-[var(--text-tertiary)] transition-transform",
            !isExpanded && "-rotate-90",
          )}
          aria-hidden="true"
        />
      ) : null}
    </>
  );

  return (
    <div className="flex min-w-0 flex-col gap-2">
      {isInteractive ? (
        <button
          type="button"
          className="lemma-assistant-run-trace-header flex w-fit max-w-full items-center gap-1.5 border-0 bg-transparent p-0 text-left transition-colors"
          onClick={onToggle}
          aria-expanded={isExpanded}
        >
          {content}
        </button>
      ) : (
        <div className="lemma-assistant-run-trace-header flex w-fit max-w-full items-center gap-1.5">
          {content}
        </div>
      )}
      <div className="h-px w-full bg-[var(--row-border)]" aria-hidden="true" />
    </div>
  );
}

export function ToolActivityRollup({
  detailParts,
  collapsedLabel,
  isRunActive,
  onNavigateResource,
  onResolveUserApproval,
  renderToolInvocation,
  message,
  activeConversationId,
}: {
  detailParts: Array<
    Extract<AssistantMessagePart, { type: "tool" }>
    | Extract<AssistantMessagePart, { type: "reasoning" }>
  >;
  collapsedLabel?: string;
  isRunActive?: boolean;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
  onResolveUserApproval?: (approvalId: string, decision: UserApprovalDecision, response?: Record<string, unknown> | null) => Promise<void>;
  renderToolInvocation?: (args: AssistantToolRenderArgs) => ReactNode;
  message: AssistantRenderableMessage;
  activeConversationId: string | null;
}) {
  const [activeDetailId, setActiveDetailId] = useState<string | null>(null);
  const [expandedGroupId, setExpandedGroupId] = useState<string | null>(null);
  const [isExpanded, setIsExpanded] = useState(false);
  const subagentToolParts = detailParts.filter((part): part is ToolActivityPart => (
    part.type === "tool" && isSubagentLifecycleToolName(part.toolInvocation.toolName)
  ));
  const visibleDetailParts = detailParts.filter((part) => (
    part.type !== "tool" || !isSubagentLifecycleToolName(part.toolInvocation.toolName)
  ));
  const toolParts = visibleDetailParts.filter((part): part is Extract<AssistantMessagePart, { type: "tool" }> => part.type === "tool");
  const reasoningParts = visibleDetailParts.filter((part): part is Extract<AssistantMessagePart, { type: "reasoning" }> => part.type === "reasoning");
  const totalThoughtDurationMs = reasoningParts.reduce((total, part) => total + (part.durationMs ?? 0), 0);
  const hasRunHeader = Boolean(collapsedLabel);
  const activitySummary = formatToolActivitySummary(toolParts);
  const activityBlocks = groupActivityDetailParts(visibleDetailParts);
  // Only a *single* tool-group relies on the master header to summarize/collapse
  // it (its body renders the calls bare). When other blocks are interleaved
  // (e.g. a trailing "Thought"), each long group renders its own summary button,
  // so a master header would just repeat that group's "×N" label — skip it.
  const onlyBlock = activityBlocks.length === 1 ? activityBlocks[0] : null;
  const hasLongToolGroup = onlyBlock?.type === "tool-group" && onlyBlock.parts.length > 2;
  const hasPendingUserInteraction = toolParts.some((part) => {
    const invocation = part.toolInvocation;
    if (!isUserInteractionToolName(invocation.toolName)) return false;
    const resultData = (invocation.result || {}) as ToolCardResult;
    return invocation.state !== "result" && !userApprovalResolvedDecision(resultData);
  });
  const shouldShowHeader = hasRunHeader
    || hasLongToolGroup
    || (Boolean(isRunActive) && !hasPendingUserInteraction);
  const failedCount = toolParts.filter((part) => (
    part.toolInvocation.state === "result"
    && !isLongRunningToolResult(part.toolInvocation)
    && part.toolInvocation.result?.success === false
  )).length;
  const isWorking = Boolean(isRunActive) || reasoningParts.some((part) => part.state === "streaming");
  const isSingleDetail = visibleDetailParts.length === 1;
  const completionSummary = activitySummary
    ? activitySummary
    : totalThoughtDurationMs > 0
      ? `Thought for ${formatDurationCompact(totalThoughtDurationMs)}`
      : "Completed";
  const summary = isWorking
    ? (activitySummary ? `Working · ${activitySummary}` : "Thinking")
    : `${completionSummary}${failedCount > 0 ? ` · ${failedCount} failed` : ""}`;
  const collapsedSummary = `${collapsedLabel || summary}${isWorking && failedCount > 0 ? ` · ${failedCount} failed` : ""}`;

  if (visibleDetailParts.length === 0 && subagentToolParts.length === 0) return null;

  const isTraceExpanded = isExpanded || hasPendingUserInteraction;

  const renderToolActivityPart = (part: ToolActivityPart): ReactNode => {
    const invocation = part.toolInvocation;
    const isSelected = activeDetailId === invocation.toolCallId;
    if (isRenderableUserInteractionInvocation(invocation)) {
      const resultData = (invocation.result || {}) as ToolCardResult;
      const isResolved = invocation.state === "result" || !!userApprovalResolvedDecision(resultData);
      if (isResolved) {
        return (
          <div key={part.id} className="min-w-0">
            <InlineUserApprovalCall
              invocation={invocation}
              isSelected={isSelected}
              onClick={() => setActiveDetailId((prev) => (prev === invocation.toolCallId ? null : invocation.toolCallId))}
            />
            {isSelected ? (
              <div className="mt-2 pl-2">
                <ToolDetailsPanel
                  toolCallId={invocation.toolCallId}
                  toolName={invocation.toolName}
                  args={invocation.args}
                  state={invocation.state}
                  result={invocation.result}
                  onNavigateResource={onNavigateResource}
                  onResolveUserApproval={onResolveUserApproval}
                  renderToolInvocation={renderToolInvocation}
                  message={message}
                  activeConversationId={activeConversationId}
                />
              </div>
            ) : null}
          </div>
        );
      }

      return (
        <div key={part.id} className="min-w-0">
          <InlineUserApprovalCall
            invocation={invocation}
            isSelected={isSelected}
            onClick={() => setActiveDetailId((prev) => (prev === invocation.toolCallId ? null : invocation.toolCallId))}
          />
        </div>
      );
    }

    return (
      <div key={part.id} className="min-w-0">
        <InlineToolCall
          invocation={invocation}
          isSelected={isSelected}
          onClick={() => setActiveDetailId((prev) => (prev === invocation.toolCallId ? null : invocation.toolCallId))}
        />
        {isSelected ? (
          <div className="mt-2 pl-2">
            <ToolDetailsPanel
              toolCallId={invocation.toolCallId}
              toolName={invocation.toolName}
              args={invocation.args}
              state={invocation.state}
              result={invocation.result}
              onNavigateResource={onNavigateResource}
              onResolveUserApproval={onResolveUserApproval}
              renderToolInvocation={renderToolInvocation}
              message={message}
              activeConversationId={activeConversationId}
            />
          </div>
        ) : null}
      </div>
    );
  };

  return (
    <div className="flex min-w-0 flex-col gap-3">
      {subagentToolParts.length > 0 ? (
        <SubagentActivityRollup
          parts={subagentToolParts}
          parentConversationId={activeConversationId}
          isRunActive={isRunActive}
        />
      ) : null}
      {visibleDetailParts.length > 0 ? (
        <div className={cn("flex min-w-0 flex-col", hasRunHeader ? "gap-3" : "gap-1.5")} data-single={isSingleDetail ? "true" : "false"}>
      {hasRunHeader ? (
        <RunTraceHeader
          label={collapsedSummary}
          isExpanded={isTraceExpanded}
          isInteractive
          onToggle={() => setIsExpanded((prev) => !prev)}
        />
      ) : shouldShowHeader ? (
        <button
          type="button"
          className="lemma-assistant-tool-rollup-toggle-button inline-flex max-w-full cursor-pointer items-center gap-2.5 border-0 bg-transparent p-0 text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
          onClick={() => setIsExpanded((prev) => !prev)}
          aria-expanded={isTraceExpanded}
          aria-label={isTraceExpanded ? "Hide tool activity details" : "Show tool activity details"}
        >
          <span className="truncate text-sm">{collapsedSummary}</span>
        </button>
      ) : null}

      {!shouldShowHeader || isTraceExpanded ? (
        <div className={cn(
          "flex flex-col gap-1.5",
          isSingleDetail && "gap-1",
        )}>
          {activityBlocks.length === 1 && activityBlocks[0].type === "tool-group" ? (
            <div className="flex flex-col gap-1.5">
              {activityBlocks[0].parts.map(renderToolActivityPart)}
            </div>
          ) : activityBlocks.map((block) => {
            if (block.type === "reasoning") {
              const part = block.part;
              return (
                <ReasoningPartCard
                  key={`thinking-${part.id}`}
                  text={part.text}
                  isStreaming={part.state === "streaming"}
                  durationMs={part.durationMs}
                />
              );
            }

            const hasUnresolvedApproval = block.parts.some((part) => {
              const invocation = part.toolInvocation;
              if (!isUserInteractionToolName(invocation.toolName)) return false;
              const resultData = (invocation.result || {}) as ToolCardResult;
              return invocation.state !== "result" && !userApprovalResolvedDecision(resultData);
            });

            if (hasUnresolvedApproval) {
              return (
                <div key={block.id} className="flex flex-col gap-1.5">
                  {block.parts.map(renderToolActivityPart)}
                </div>
              );
            }

            if (block.parts.length <= 2) {
              return (
                <div key={block.id} className="flex flex-col gap-1.5">
                  {block.parts.map(renderToolActivityPart)}
                </div>
              );
            }

            const groupSummary = formatToolActivitySummary(block.parts) || `Used ${pluralize(block.parts.length, "tool")}`;
            const isGroupSelected = expandedGroupId === block.id;
            return (
              <div key={block.id} className="flex flex-col gap-1.5">
                <button
                  type="button"
                  onClick={() => setExpandedGroupId((prev) => (prev === block.id ? null : block.id))}
                  className="lemma-assistant-tool-group-button inline-flex max-w-full items-center gap-2 border-0 bg-transparent p-0 text-left text-sm text-[var(--text-secondary)] transition-colors hover:text-[var(--text-primary)]"
                >
                  <span className="truncate">{groupSummary}</span>
                </button>
                {isGroupSelected ? (
                  <div className="flex flex-col gap-1.5">
                    {block.parts.map(renderToolActivityPart)}
                  </div>
                ) : null}
              </div>
            );
          })}
        </div>
      ) : null}
        </div>
      ) : null}
    </div>
  );
}

export function TextBlockWithCopy({
  text,
  timestamp,
  children,
  showActions = true,
}: {
  text: string;
  timestamp?: { text: string; dateTime: string } | null;
  children: ReactNode;
  /** Whether to show the copy/timestamp bar. Trace (non-final) text drops it,
   * which also removes the reserved vertical gap it would otherwise leave. */
  showActions?: boolean;
}) {
  const [copied, setCopied] = useState(false);
  const handleCopy = async () => {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch { /* clipboard access denied */ }
  };
  if (!showActions) return <>{children}</>;
  return (
    <div className="group">
      {children}
      <div className="mt-1.5 flex items-center gap-2 opacity-0 transition-opacity group-hover:opacity-100">
        <button
          onClick={handleCopy}
          className="inline-flex items-center gap-1 text-xs text-[var(--text-tertiary)] hover:text-[var(--text-secondary)] transition-colors"
          title="Copy message"
        >
          {copied
            ? <Check className="size-3 text-[var(--state-success)]" />
            : <Copy className="size-3" />}
          <span>{copied ? "Copied" : "Copy"}</span>
        </button>
        {timestamp ? (
          <time className="text-xs text-[var(--text-tertiary)]" dateTime={timestamp.dateTime}>
            {timestamp.text}
          </time>
        ) : null}
      </div>
    </div>
  );
}
