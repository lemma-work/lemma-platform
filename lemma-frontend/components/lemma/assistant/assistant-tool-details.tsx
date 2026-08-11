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
import { Check, Copy } from "@/components/ui/icons";
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
  toolCallLabelParts,
  toolCallPrimaryLabel,
} from "./assistant-format";
import { DetailsWithCopy, contextualToolDetails } from "./assistant-tool-cards";
import { ReasoningPartCard, TraceDisclosureLine } from "./assistant-parts";
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
	              variant="secondary"
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
  const { verb, object } = toolCallLabelParts(invocation.toolName, invocation.args);

  return (
    <button
      type="button"
      onClick={onClick}
      className="lemma-assistant-inline-tool-button inline-flex max-w-full items-baseline gap-1.5 border-0 bg-transparent p-0 text-left transition-colors"
      data-state={isExecuting ? "executing" : isFailed ? "failed" : "complete"}
      data-selected={isSelected}
    >
      {/* No icon. A glyph in front of every row added a column of noise down the
          left of the trace without saying anything the label did not, and it was
          the widest source of crowding once a run had a dozen steps.

          Wraps, and reads the same whether or not it is the one running: a
          per-row shimmer made the executing row a different weight and colour
          from the rows above it, so a list of tool calls never looked like one
          list. Activity is the transcript's single bottom indicator's job. */}
      {verb ? <span className="shrink-0 opacity-70">{verb}</span> : null}
      <span className="min-w-0 break-words">{object}</span>
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

/** A one-line summary for a block of tool calls: the tool's own name when the
 * block is all one kind ("Terminal command ×3"), otherwise a plain count. */
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

  // One kind of tool names itself ("Terminal command ×3"). Several kinds used to
  // be glued together — "Updated plan and Terminal command ×2" — which is longer
  // than the rows it summarises and harder to read than counting them.
  if (order.length === 1) return phraseFor(order[0]);
  return `Ran ${pluralize(toolParts.length, "step")}`;
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
  // The run rollup is the same disclosure line as a thought or a tool group; it
  // is only distinguished by the rule under it, which separates a whole run's
  // trace from the answer that follows.
  return (
    <TraceDisclosureLine
      label={label}
      isExpanded={isExpanded}
      onToggle={isInteractive ? onToggle : undefined}
      rule
    />
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
  // Closed, and it stays closed until the reader opens it. The header line above
  // names what happened ("Ran 3 commands"), which is the summary — the cards are
  // the detail behind it.
  const [isExpanded, setIsExpanded] = useState(false);
  const subagentToolParts = detailParts.filter((part): part is ToolActivityPart => (
    part.type === "tool" && isSubagentLifecycleToolName(part.toolInvocation.toolName)
  ));
  const visibleDetailParts = detailParts.filter((part) => (
    part.type !== "tool" || !isSubagentLifecycleToolName(part.toolInvocation.toolName)
  ));
  const toolParts = visibleDetailParts.filter((part): part is Extract<AssistantMessagePart, { type: "tool" }> => part.type === "tool");
  const reasoningParts = visibleDetailParts.filter((part): part is Extract<AssistantMessagePart, { type: "reasoning" }> => part.type === "reasoning");
  const hasCompleteThoughtDuration = reasoningParts.length > 0
    && reasoningParts.every((part) => typeof part.durationMs === "number" && part.durationMs > 0);
  const totalThoughtDurationMs = hasCompleteThoughtDuration
    ? reasoningParts.reduce((total, part) => total + (part.durationMs ?? 0), 0)
    : 0;
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
  // A header summarises several things. One tool call is already its own
  // summary, so wrapping it in "Terminal command ⌄" asks the reader to open a
  // drawer to find the single line it was hiding.
  //
  // Deliberately not a function of `isRunActive`: that made a rollup unfurl its
  // cards the instant a run ended. What a rollup looks like depends only on how
  // much it holds, which does not change when the run does.
  void hasRunHeader;
  void hasLongToolGroup;
  const shouldShowHeader = visibleDetailParts.length + subagentToolParts.length > 1;
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
  // Only claim "Thinking" for a thought this rollup actually holds. When the
  // thought renders as a sibling card instead - a message with text keeps its
  // reasoning unfolded - that card is already showing the word, and a header
  // repeating it reads as the assistant thinking twice.
  const summary = isWorking
    ? (activitySummary
      ? `Working · ${activitySummary}`
      : reasoningParts.length > 0 ? "Thinking" : "Working")
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
        <TraceDisclosureLine
          label={collapsedSummary}
          isExpanded={isTraceExpanded}
          onToggle={() => setIsExpanded((prev) => !prev)}
        />
      ) : null}

      {!shouldShowHeader || isTraceExpanded ? (
        // No frame here. A tool's own detail card already draws one — a terminal
        // card brings its own chrome, title bar and status — so wrapping the
        // group in a second box produced a box inside a box inside a box. The
        // group's extent is shown by the indent below and by its header above,
        // both of which cost nothing.
        <div className={cn(
          "flex flex-col gap-1",
          isSingleDetail && "gap-1",
          shouldShowHeader && "border-l border-[color:var(--row-border)] pl-3",
        )}>
          {activityBlocks.length === 1 && activityBlocks[0].type === "tool-group" ? (
            <div className="flex flex-col gap-1">
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
                  showSummary={!shouldShowHeader}
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
                <div key={block.id} className="flex flex-col gap-1">
                  {block.parts.map(renderToolActivityPart)}
                </div>
              );
            }

            if (block.parts.length <= 2) {
              return (
                <div key={block.id} className="flex flex-col gap-1">
                  {block.parts.map(renderToolActivityPart)}
                </div>
              );
            }

            const groupSummary = formatToolActivitySummary(block.parts) || `Used ${pluralize(block.parts.length, "tool")}`;
            const isGroupSelected = expandedGroupId === block.id;
            return (
              <div key={block.id} className="flex flex-col gap-1">
                <TraceDisclosureLine
                  label={groupSummary}
                  isExpanded={isGroupSelected}
                  onToggle={() => setExpandedGroupId((prev) => (prev === block.id ? null : block.id))}
                />
                {isGroupSelected ? (
                  <div className="flex flex-col gap-1">
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
