"use client";

// Display-resource cards extracted from assistant-message-group.tsx: the resource
// label/kind/title helpers, the row → cards collection logic, the inline query
// preview, the <DisplayResourceCards> list, plus the local-href helpers shared
// with the tool-details panel. No runtime cycle with the rest of the cluster.

import { useMemo } from "react";
import { useQuery } from "@tanstack/react-query";
import { dedupToolInvocations, isFinalAnswerMessage } from "lemma-sdk";
import { Database } from "@/components/ui/icons";
import { cn } from "@/lib/utils";
import { InlineLoader } from "@/components/brand/loader";
import {
  buildDisplayResourceHref,
  extractDisplayResourceFromInvocation,
  type DisplayResourceRequest,
} from "@/lib/assistant/display-resource";
import { CONVERSATION_PRESENTED_RESOURCE_PARAM } from "@/lib/assistant/conversation-presentation";
import { getLemmaClient } from "@/lib/sdk/lemma-client";
import { fileNameFromPath } from "./assistant-format";
import { displayResourceIcon } from "./assistant-parts";
import { InlineWidget } from "./inline-widget";
import type { AssistantRenderableMessage } from "lemma-sdk/react";
import type {
  CompletedDisplayResourceCard,
  DisplayMessageRow,
} from "./assistant-experience";

export function currentPodIdFromBrowserPath(): string | null {
  if (typeof window === "undefined") return null;
  const match = window.location.pathname.match(/^\/pod\/([^/]+)/);
  return match?.[1] ? decodeURIComponent(match[1]) : null;
}

// True on the full chat route (/pod/{id}/conversations/...). Elsewhere the
// assistant is a side panel and a widget may already be open full in the main
// area — there we fall back to the navigate card instead of iframing inline.
export function isConversationPageFromBrowserPath(): boolean {
  if (typeof window === "undefined") return false;
  return /^\/pod\/[^/]+\/conversations(?:\/|$)/.test(window.location.pathname);
}

export function canonicalLocalHref(value: string): string | null {
  if (typeof window === "undefined") return null;
  try {
    const url = new URL(value, window.location.origin);
    const entries = Array.from(url.searchParams.entries())
      .sort(([leftKey, leftValue], [rightKey, rightValue]) => (
        leftKey === rightKey ? leftValue.localeCompare(rightValue) : leftKey.localeCompare(rightKey)
      ));
    const query = entries.map(([key, entryValue]) => `${encodeURIComponent(key)}=${encodeURIComponent(entryValue)}`).join("&");
    return `${url.pathname}${query ? `?${query}` : ""}`;
  } catch {
    return null;
  }
}

export function isCurrentBrowserHref(value: string | null): boolean {
  if (!value || typeof window === "undefined") return false;
  const current = canonicalLocalHref(`${window.location.pathname}${window.location.search}`);
  const target = canonicalLocalHref(value);
  if (!!current && !!target && current === target) return true;

  const presented = new URLSearchParams(window.location.search)
    .get(CONVERSATION_PRESENTED_RESOURCE_PARAM);
  const presentedTarget = presented ? canonicalLocalHref(presented) : null;
  return !!presentedTarget && !!target && presentedTarget === target;
}

export function humanizeResourceName(value: string): string {
  const cleaned = value
    .replace(/\.[^.]+$/g, "")
    .replace(/[_-]+/g, " ")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return value;
  return cleaned.charAt(0).toUpperCase() + cleaned.slice(1);
}

export function displayResourceKind(request: DisplayResourceRequest): string {
  switch (request.type) {
    case "FILE":
      return "File";
    case "TABLE":
      return request.name ? "Table" : "Tables";
    case "AGENT":
      return request.name ? "Agent" : "Agents";
    case "FUNCTION":
      return request.name ? "Function" : "Functions";
    case "WORKFLOW":
      return request.name ? "Workflow" : "Workflows";
    case "APP":
      return request.name ? "App" : "Apps";
    case "SCHEDULE":
      return request.name ? "Schedule" : "Schedules";
    case "WIDGET":
      return "View";
    default:
      return "Resource";
  }
}

export function displayResourceTitle(request: DisplayResourceRequest): string {
  if (request.type === "FILE" && request.path) return fileNameFromPath(request.path);
  if (request.name) return humanizeResourceName(request.name);
  if (request.type === "WIDGET") return "Interactive view";
  return displayResourceKind(request);
}

export function compactResourcePath(path: string): string {
  const normalized = path.replace(/^\/pod\/?/g, "").replace(/^\/+/g, "");
  if (!normalized) return path;
  const parts = normalized.split("/").filter(Boolean);
  if (parts.length <= 2) return normalized;
  return `${parts[0]} / ${parts[parts.length - 1]}`;
}

export function displayResourceActionLabel(): string {
  return "Open";
}

export function displayResourceDetailText(request: DisplayResourceRequest): string {
  if (request.path) return compactResourcePath(request.path);
  if (request.publicUrl) {
    try {
      return new URL(request.publicUrl).host;
    } catch {
      return request.publicUrl;
    }
  }
  if (request.query) return "Query result";
  if (request.filters?.length) return `${request.filters.length} filter${request.filters.length === 1 ? "" : "s"} applied`;
  if (request.type === "WIDGET") return "Generated by the assistant";
  if (request.name) return displayResourceKind(request);
  return "Opened in the workspace";
}

export function displayResourceAriaLabel(request: DisplayResourceRequest): string {
  return `${displayResourceActionLabel()} ${displayResourceKind(request).toLowerCase()} ${displayResourceTitle(request)}`;
}

export function rowIntersectsRange(row: DisplayMessageRow, start: number, end: number): boolean {
  return row.sourceIndexes.some((sourceIndex) => sourceIndex >= start && sourceIndex <= end);
}

export function collectDisplayResourcesForRows({
  activeConversationId,
  podId,
  rows,
  start,
  end,
}: {
  activeConversationId: string | null;
  podId: string | null;
  rows: DisplayMessageRow[];
  start: number;
  end: number;
}): CompletedDisplayResourceCard[] {
  if (!podId) return [];

  const seen = new Set<string>();
  const cards: CompletedDisplayResourceCard[] = [];

  rows.forEach((row) => {
    if (!rowIntersectsRange(row, start, end)) return;

    dedupToolInvocations(row.message).forEach((invocation) => {
      if (invocation.state !== "result") return;
      if (invocation.result?.success === false) return;

      const displayResource = extractDisplayResourceFromInvocation(invocation);
      if (!displayResource || seen.has(displayResource.toolCallId)) return;
      seen.add(displayResource.toolCallId);
      cards.push({
        toolCallId: displayResource.toolCallId,
        request: displayResource.request,
        href: buildDisplayResourceHref({
          podId,
          request: displayResource.request,
          conversationId: activeConversationId,
          toolCallId: displayResource.toolCallId,
        }),
      });
    });
  });

  return cards;
}

export function displayResourceAnchorRowIndex({
  end,
  finalIndex,
  rows,
  start,
}: {
  end: number;
  finalIndex: number | null;
  rows: DisplayMessageRow[];
  start: number;
}): number | null {
  if (typeof finalIndex === "number") {
    const finalRowIndex = rows.findIndex((row) => row.sourceIndexes.includes(finalIndex));
    if (finalRowIndex >= 0) return finalRowIndex;
  }

  for (let rowIndex = rows.length - 1; rowIndex >= 0; rowIndex -= 1) {
    const row = rows[rowIndex];
    if (row.message.role !== "assistant") continue;
    if (!rowIntersectsRange(row, start, end)) continue;
    return rowIndex;
  }

  return null;
}

export function collectDisplayResourceCardsByRow({
  activeConversationId,
  isConversationBusy,
  messages,
  podId,
  rows,
}: {
  activeConversationId: string | null;
  isConversationBusy: boolean;
  messages: AssistantRenderableMessage[];
  podId: string | null;
  rows: DisplayMessageRow[];
}): Map<number, CompletedDisplayResourceCard[]> {
  const cardsByRow = new Map<number, CompletedDisplayResourceCard[]>();
  if (!podId || rows.length === 0 || messages.length === 0) return cardsByRow;

  for (let start = 0; start < messages.length; start += 1) {
    const end = (() => {
      for (let index = start + 1; index < messages.length; index += 1) {
        if (messages[index].role === "user") return index - 1;
      }
      return messages.length - 1;
    })();

    const sectionMessages = messages.slice(start, end + 1);
    const hasAssistantMessage = sectionMessages.some((message) => message.role === "assistant");
    if (!hasAssistantMessage) {
      start = end;
      continue;
    }

    const finalOffset = sectionMessages.findLastIndex((message) => (
      message.role === "assistant" && isFinalAnswerMessage(message)
    ));
    const finalIndex = finalOffset >= 0 ? start + finalOffset : null;
    const isLatestSection = end >= messages.length - 1;
    const isComplete = typeof finalIndex === "number" || !isLatestSection || !isConversationBusy;
    if (!isComplete) {
      start = end;
      continue;
    }

    const sectionRows = rows.filter((row) => rowIntersectsRange(row, start, end));
    const cards = collectDisplayResourcesForRows({
      activeConversationId,
      podId,
      rows: sectionRows,
      start,
      end,
    });
    if (cards.length === 0) {
      start = end;
      continue;
    }

    const anchorRowIndex = displayResourceAnchorRowIndex({
      end,
      finalIndex,
      rows,
      start,
    });
    if (typeof anchorRowIndex === "number") {
      cardsByRow.set(anchorRowIndex, cards);
    }

    start = end;
  }

  return cardsByRow;
}

export function DisplayResourceCards({
  cards,
  activeConversationId,
  onNavigateResource,
}: {
  cards: CompletedDisplayResourceCard[];
  activeConversationId: string | null;
  onNavigateResource?: (resourceType: string, resourceId: string, meta?: Record<string, unknown>) => void;
}) {
  if (cards.length === 0) return null;

  const podId = currentPodIdFromBrowserPath();
  const onConversationPage = isConversationPageFromBrowserPath();

  return (
    <div className="flex w-full max-w-full flex-col gap-1.5">
      {cards.map((card) => {
        const isHere = isCurrentBrowserHref(card.href);
        const detail = displayResourceDetailText(card.request);
        const isInlineQuery = card.request.type === "TABLE" && !!card.request.query;
        const isWidget = card.request.type === "WIDGET";
        // Only iframe the widget inline on the full chat page; in the side panel
        // (e.g. when the widget is already open full) use the navigate card.
        const renderWidgetInline = isWidget && !!podId && onConversationPage;
        const canOpen = !isInlineQuery && !renderWidgetInline && !!onNavigateResource && !!card.href;
        const kind = displayResourceKind(card.request);
        const title = displayResourceTitle(card.request);
        const actionLabel = displayResourceActionLabel();
        const expandResource = () => onNavigateResource?.("display_resource", card.toolCallId, {
          request: card.request,
          conversationId: activeConversationId,
        });
        const rowClassName = cn(
          "group flex w-full max-w-full items-center gap-2.5 rounded-lg border px-2.5 py-2 text-left transition-gentle",
          "border-[color:color-mix(in_srgb,var(--row-border)_42%,transparent)]",
          "bg-[color:color-mix(in_srgb,var(--surface-1)_82%,transparent)]",
          "shadow-[var(--shadow-xs)]",
          canOpen && "cursor-pointer hover:border-[var(--action-primary)] hover:bg-[var(--surface-1)] hover:shadow-[var(--shadow-xs)] focus-ring",
          !canOpen && "cursor-default",
        );
        const content = (
          <>
            <span className="flex size-7 flex-shrink-0 items-center justify-center rounded-md text-[var(--text-tertiary)] transition-colors group-hover:text-[var(--action-primary)]">
              {displayResourceIcon(card.request)}
            </span>
            <span className="min-w-0 flex-1">
              <span className="flex min-w-0 items-baseline gap-1.5">
                <span className="truncate text-sm font-medium text-[var(--text-primary)]">{title}</span>
                <span className="text-xs text-[var(--text-tertiary)]">{kind}</span>
              </span>
              <span className="mt-0.5 block truncate text-xs text-[var(--text-secondary)]">
                {detail}
              </span>
            </span>
            {canOpen ? (
              <span className="ml-1 flex flex-shrink-0 items-center rounded-md px-1.5 py-1 text-xs font-medium text-[var(--action-primary)] opacity-0 transition-opacity group-hover:opacity-100 group-focus-visible:opacity-100">
                {isHere ? "Viewing" : actionLabel}
              </span>
            ) : null}
          </>
        );

        if (renderWidgetInline && podId) {
          const canExpand = !!onNavigateResource && !!card.href;
          return (
            <InlineWidget
              key={card.toolCallId}
              podId={podId}
              conversationId={activeConversationId}
              toolCallId={card.toolCallId}
              externalSrc={card.request.publicUrl}
              title={title}
              loadingMessages={card.request.loadingMessages}
              variant="inline"
              onExpand={canExpand ? expandResource : undefined}
            />
          );
        }

        if (isInlineQuery) {
          return (
            <div key={card.toolCallId} className="flex w-full max-w-full flex-col gap-1.5">
              <div className={rowClassName}>
                {content}
              </div>
              <DisplayResourceQueryPreview request={card.request} />
            </div>
          );
        }

        return canOpen ? (
          <button
            key={card.toolCallId}
            type="button"
            aria-label={displayResourceAriaLabel(card.request)}
            onClick={() => onNavigateResource?.("display_resource", card.toolCallId, {
              request: card.request,
              conversationId: activeConversationId,
            })}
            className={rowClassName}
          >
            {content}
          </button>
        ) : (
          <div key={card.toolCallId} className={rowClassName}>
            {content}
          </div>
        );
      })}
    </div>
  );
}

export function queryPreviewCellValue(value: unknown): string {
  if (value === null || value === undefined) return "";
  if (typeof value === "string") return value;
  if (typeof value === "number" || typeof value === "boolean") return String(value);
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}

export function DisplayResourceQueryPreview({
  request,
}: {
  request: DisplayResourceRequest;
}) {
  const podId = currentPodIdFromBrowserPath();
  const query = request.query?.trim() || "";
  const { data, isLoading, error } = useQuery({
    queryKey: ["display-resource-query-preview", podId, query],
    queryFn: async () => {
      if (!podId || !query) return { items: [], total: 0 };
      return getLemmaClient(podId).datastore.query(query);
    },
    enabled: Boolean(podId && query),
    staleTime: 60_000,
  });

  const rows = useMemo(() => data?.items || [], [data?.items]);
  const columns = useMemo(() => {
    const seen = new Set<string>();
    rows.slice(0, 8).forEach((row) => {
      Object.keys(row).forEach((key) => {
        if (seen.size < 6) seen.add(key);
      });
    });
    return Array.from(seen);
  }, [rows]);
  const visibleRows = rows.slice(0, 8);

  return (
    <div className="overflow-hidden rounded-lg border border-[color:color-mix(in_srgb,var(--row-border)_42%,transparent)] bg-[var(--surface-1)] shadow-[var(--shadow-xs)]">
      <div className="flex items-center justify-between gap-3 border-b border-[color:color-mix(in_srgb,var(--row-border)_35%,transparent)] px-3 py-2">
        <div className="flex min-w-0 items-center gap-2">
          <span className="flex size-6 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)] text-[var(--text-tertiary)]">
            <Database className="size-3.5" />
          </span>
          <span className="truncate text-xs font-medium text-[var(--text-primary)]">
            Query result
          </span>
        </div>
        <span className="shrink-0 text-xs text-[var(--text-tertiary)]">
          {isLoading ? "Loading" : `${data?.total ?? visibleRows.length} rows`}
        </span>
      </div>

      {isLoading ? (
        <div className="flex items-center gap-2 px-3 py-3 text-xs text-[var(--text-secondary)]">
          <InlineLoader />
          Running query
        </div>
      ) : error ? (
        <div className="px-3 py-3 text-xs text-[var(--state-error)]">
          Query could not run.
        </div>
      ) : visibleRows.length === 0 || columns.length === 0 ? (
        <div className="px-3 py-3 text-xs text-[var(--text-secondary)]">
          No rows returned.
        </div>
      ) : (
        <div className="max-h-72 overflow-auto">
          <table className="min-w-full table-fixed text-left text-xs">
            <thead className="sticky top-0 bg-[var(--surface-1)]">
              <tr>
                {columns.map((column) => (
                  <th
                    key={column}
                    className="border-b border-[color:color-mix(in_srgb,var(--row-border)_35%,transparent)] px-3 py-2 font-medium text-[var(--text-tertiary)]"
                  >
                    <span className="block truncate">{column}</span>
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {visibleRows.map((row, index) => (
                <tr key={index} className="border-b border-[color:color-mix(in_srgb,var(--row-border)_24%,transparent)] last:border-b-0">
                  {columns.map((column) => (
                    <td key={column} className="px-3 py-2 text-[var(--text-secondary)]">
                      <span className="block max-w-48 truncate">
                        {queryPreviewCellValue(row[column])}
                      </span>
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
