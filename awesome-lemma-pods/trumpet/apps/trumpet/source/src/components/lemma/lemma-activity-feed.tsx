import * as React from "react"
import {
  Activity,
  Calendar,
  Database,
  FileText,
  MessageSquare,
  RefreshCw,
  User,
  Zap,
} from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import { Skeleton } from "@/components/lemma/ui/skeleton"
import { useRecords } from "lemma-sdk/react"
import type { LemmaClient } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import { enumPillClasses, type EnumColorMap } from "./activity-enum-utils"
import {
  activityRadiusClassName,
  type LemmaActivityAppearance,
  type LemmaActivityDensity,
  type LemmaActivityRadius,
} from "./activity-style-utils"

export type { LemmaActivityAppearance, LemmaActivityDensity, LemmaActivityRadius } from "./activity-style-utils"

export interface ActivitySource {
  tableName: string
  label?: string
  timestampField: string
  descriptionField?: string
  typeField?: string
  userField?: string
  iconField?: string
  filters?: Array<{ field: string; op: string; value: unknown }>
  limit?: number
}

export type ActivityEventType = "create" | "update" | "delete" | "comment" | "workflow" | "system" | "custom"

export interface LemmaActivityFeedProps {
  client: LemmaClient
  podId?: string
  enabled?: boolean

  sources: ActivitySource[]
  enumColorMap?: EnumColorMap

  appearance?: LemmaActivityAppearance
  density?: LemmaActivityDensity
  radius?: LemmaActivityRadius

  title?: React.ReactNode
  headerActions?: React.ReactNode
  className?: string
  onEventClick?: (event: ActivityEvent, source: ActivitySource) => void
}

export interface ActivityEvent {
  id: string
  timestamp: Date
  description: string
  type: ActivityEventType
  tableName: string
  source: string
  user?: string
  icon?: string
  record: Record<string, unknown>
}

export function LemmaActivityFeed({
  client,
  podId,
  enabled = true,
  sources,
  enumColorMap,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  title,
  headerActions,
  className,
  onEventClick,
}: LemmaActivityFeedProps) {
  const sourceResults = sources.map((source) => ({
    source,
    state: useRecords({
      client,
      podId,
      tableName: source.tableName,
      filters: source.filters as Array<{ field: string; op: string; value: unknown }> | undefined,
      limit: source.limit ?? 50,
      sortBy: source.timestampField,
      order: "desc",
      enabled,
    }),
  }))

  const allEvents = React.useMemo(() => {
    const events: ActivityEvent[] = []
    for (const { source, state } of sourceResults) {
      for (const record of state.records) {
        const tsVal = record[source.timestampField]
        if (tsVal == null) continue
        const timestamp = new Date(String(tsVal))
        if (Number.isNaN(timestamp.getTime())) continue

        const desc = source.descriptionField
          ? String(record[source.descriptionField] ?? "")
          : inferDescription(record, source.tableName)

        const type = source.typeField
          ? inferEventType(String(record[source.typeField] ?? ""))
          : inferEventTypeFromRecord(record)

        const user = source.userField ? String(record[source.userField] ?? "") : undefined

        events.push({
          id: String(record.id ?? `${source.tableName}-${events.length}`),
          timestamp,
          description: desc || `Record updated in ${source.tableName}`,
          type,
          tableName: source.tableName,
          source: source.label ?? source.tableName,
          user,
          icon: source.iconField ? String(record[source.iconField] ?? "") : undefined,
          record,
        })
      }
    }
    events.sort((a, b) => b.timestamp.getTime() - a.timestamp.getTime())
    return events.slice(0, 100)
  }, [sourceResults, sources])

  const groupedEvents = React.useMemo(() => {
    const groups: Array<{ label: string; events: ActivityEvent[] }> = []
    let currentLabel = ""
    let currentEvents: ActivityEvent[] = []

    for (const event of allEvents) {
      const label = formatDateGroup(event.timestamp)
      if (label !== currentLabel) {
        if (currentEvents.length > 0) {
          groups.push({ label: currentLabel, events: currentEvents })
        }
        currentLabel = label
        currentEvents = []
      }
      currentEvents.push(event)
    }
    if (currentEvents.length > 0) {
      groups.push({ label: currentLabel, events: currentEvents })
    }
    return groups
  }, [allEvents])

  const isLoading = sourceResults.some((r) => r.state.isLoading)
  const hasError = sourceResults.find((r) => r.state.error)

  return (
    <div
      data-appearance={appearance}
      data-density={density}
      data-radius={radius}
      className={cn("lemma-activity-feed flex h-full min-h-0 flex-col", activityRootClassName(appearance), className)}
    >
      <div className={cn("shrink-0", activityHeaderClassName(appearance))}>
        <div className={cn("flex items-center justify-between", activityToolbarClassName(density))}>
          <div className="min-w-0 flex items-center gap-3">
            <span className={cn("flex size-7 items-center justify-center border border-border/50 bg-muted/40 text-muted-foreground", activityRadiusClassName(radius, "control"))}>
              <Activity className="size-3.5" />
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold text-foreground">
                {title ?? "Activity"}
              </h1>
              <p className="text-xs text-muted-foreground">
                {allEvents.length} events from {sources.length} source{sources.length !== 1 ? "s" : ""}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {headerActions}
          </div>
        </div>
      </div>

      <div className={cn("flex-1 overflow-auto", activityContentClassName(density))}>
        {hasError ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
            <p className="text-sm text-destructive">{hasError.state.error!.message}</p>
            <Button variant="outline" size="sm" onClick={() => hasError.state.refresh()}>
              <RefreshCw className="mr-2 size-3.5" />
              Retry
            </Button>
          </div>
        ) : isLoading ? (
          <div className="space-y-4">
            {Array.from({ length: 6 }).map((_, i) => (
              <div key={i} className="flex items-start gap-3">
                <Skeleton className="size-8 rounded-full shrink-0" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-3 w-1/3" />
                </div>
              </div>
            ))}
          </div>
        ) : groupedEvents.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
            <div className={cn("flex size-10 items-center justify-center border border-border/60 bg-muted/40 text-muted-foreground", activityRadiusClassName(radius, "pill"))}>
              <Activity className="size-5" />
            </div>
            <div>
              <p className="font-medium text-foreground">No activity yet</p>
              <p className="mt-1 text-sm text-muted-foreground">Events will appear here as records are created and updated.</p>
            </div>
          </div>
        ) : (
          <div className={cn("flex flex-col", density === "compact" ? "gap-1" : density === "spacious" ? "gap-4" : "gap-2")}>
            {groupedEvents.map((group) => (
              <div key={group.label}>
                <div className={cn("sticky top-0 z-10 backdrop-blur-sm", density === "compact" ? "py-1.5" : density === "spacious" ? "py-3" : "py-2")}>
                  <span className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
                    {group.label}
                  </span>
                </div>
                <div className={cn("relative flex flex-col", density === "compact" ? "gap-1" : density === "spacious" ? "gap-3" : "gap-2")}>
                  {group.events.map((event, index) => (
                    <button
                      key={event.id}
                      type="button"
                      onClick={() => onEventClick?.(event, sources.find((s) => s.tableName === event.tableName)!)}
                      className={cn(
                        "grid grid-cols-[auto_minmax(0,1fr)] gap-3 text-left transition-colors",
                        onEventClick && "cursor-pointer hover:bg-muted/30",
                        !onEventClick && "cursor-default",
                        activityRadiusClassName(radius, "control"),
                      )}
                    >
                      <div className="flex flex-col items-center">
                        <span className={cn("flex size-7 items-center justify-center border border-border/50", eventIconBg(event.type), activityRadiusClassName(radius, "pill"))}>
                          {eventIcon(event.type, event.icon)}
                        </span>
                        {index < group.events.length - 1 && (
                          <span className="my-1 h-full min-h-4 w-px bg-border/40" />
                        )}
                      </div>
                      <div className={cn("border border-border/30 bg-muted/10", activityRadiusClassName(radius, "surface"), density === "compact" ? "p-2" : density === "spacious" ? "p-4" : "p-3")}>
                        <div className="flex items-center justify-between gap-2">
                          <p className="truncate text-sm font-medium text-foreground">
                            {event.description}
                          </p>
                          <span className={cn("shrink-0 text-[10px] text-muted-foreground", density === "compact" ? "hidden" : "")}>
                            {formatRelativeTime(event.timestamp)}
                          </span>
                        </div>
                        <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                          <span className="inline-flex items-center gap-1">
                            <Database className="size-3" />
                            {event.source}
                          </span>
                          {event.user && (
                            <span className="inline-flex items-center gap-1">
                              <User className="size-3" />
                              {event.user}
                            </span>
                          )}
                          <span className={cn("inline-flex items-center rounded-full px-1.5 py-0.5 text-[10px] font-medium", eventTypeClasses(event.type))}>
                            {event.type}
                          </span>
                          <span className={cn(density === "compact" ? "" : "hidden")}>
                            {formatRelativeTime(event.timestamp)}
                          </span>
                        </div>
                      </div>
                    </button>
                  ))}
                </div>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function eventIcon(type: ActivityEventType, customIcon?: string): React.ReactNode {
  if (customIcon) return <span className="text-xs">{customIcon}</span>
  switch (type) {
    case "create": return <Database className="size-3.5" />
    case "update": return <RefreshCw className="size-3.5" />
    case "delete": return <Database className="size-3.5" />
    case "comment": return <MessageSquare className="size-3.5" />
    case "workflow": return <Zap className="size-3.5" />
    case "system": return <FileText className="size-3.5" />
    default: return <Activity className="size-3.5" />
  }
}

function eventIconBg(type: ActivityEventType): string {
  switch (type) {
    case "create": return "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
    case "update": return "bg-blue-500/10 text-blue-600 dark:text-blue-400"
    case "delete": return "bg-destructive/10 text-destructive"
    case "comment": return "bg-violet-500/10 text-violet-600 dark:text-violet-400"
    case "workflow": return "bg-amber-500/10 text-amber-600 dark:text-amber-400"
    case "system": return "bg-muted/40 text-muted-foreground"
    default: return "bg-muted/40 text-muted-foreground"
  }
}

function eventTypeClasses(type: ActivityEventType): string {
  switch (type) {
    case "create": return "bg-emerald-500/10 text-emerald-700 dark:text-emerald-300"
    case "update": return "bg-blue-500/10 text-blue-700 dark:text-blue-300"
    case "delete": return "bg-destructive/10 text-destructive"
    case "comment": return "bg-violet-500/10 text-violet-700 dark:text-violet-300"
    case "workflow": return "bg-amber-500/10 text-amber-700 dark:text-amber-300"
    case "system": return "bg-muted/40 text-muted-foreground"
    default: return "bg-muted/40 text-muted-foreground"
  }
}

function inferEventType(value: string): ActivityEventType {
  const lower = value.toLowerCase()
  if (/creat|insert|add|new/.test(lower)) return "create"
  if (/updat|edit|modif|chang/.test(lower)) return "update"
  if (/delet|remov|trash/.test(lower)) return "delete"
  if (/comment|note|reply/.test(lower)) return "comment"
  if (/workflow|automat|trigger|run/.test(lower)) return "workflow"
  return "custom"
}

function inferEventTypeFromRecord(record: Record<string, unknown>): ActivityEventType {
  const keys = Object.keys(record)
  if (keys.some((k) => /comment|note|message|body|content/i.test(k) && typeof record[k] === "string" && String(record[k]).length > 20)) return "comment"
  return "update"
}

function inferDescription(record: Record<string, unknown>, tableName: string): string {
  const textFields = ["title", "name", "label", "subject", "summary", "description", "content", "body", "message"]
  for (const field of textFields) {
    const v = record[field]
    if (v != null && String(v).trim()) return String(v)
  }
  return `Record in ${tableName}`
}

function formatDateGroup(date: Date): string {
  const now = new Date()
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate())
  const yesterday = new Date(today)
  yesterday.setDate(yesterday.getDate() - 1)
  const eventDay = new Date(date.getFullYear(), date.getMonth(), date.getDate())

  if (eventDay.getTime() === today.getTime()) return "Today"
  if (eventDay.getTime() === yesterday.getTime()) return "Yesterday"
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
}

function formatRelativeTime(date: Date): string {
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const seconds = Math.floor(diff / 1000)
  const minutes = Math.floor(seconds / 60)
  const hours = Math.floor(minutes / 60)
  const days = Math.floor(hours / 24)

  if (seconds < 60) return "just now"
  if (minutes < 60) return `${minutes}m ago`
  if (hours < 24) return `${hours}h ago`
  if (days < 7) return `${days}d ago`
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

function activityRootClassName(appearance: LemmaActivityAppearance) {
  if (appearance === "contained") return "bg-card"
  if (appearance === "minimal" || appearance === "borderless") return "bg-transparent"
  return "bg-background"
}

function activityHeaderClassName(appearance: LemmaActivityAppearance) {
  if (appearance === "borderless") return "bg-transparent"
  if (appearance === "minimal") return "border-b border-border/15 bg-transparent"
  if (appearance === "contained") return "border-b border-border/60 bg-card"
  return "border-b border-border/40 bg-card/95"
}

function activityToolbarClassName(density: LemmaActivityDensity) {
  if (density === "compact") return "gap-2 px-3 py-2"
  if (density === "spacious") return "gap-4 px-5 py-4"
  return "gap-3 px-4 py-3"
}

function activityContentClassName(density: LemmaActivityDensity) {
  if (density === "compact") return "p-2"
  if (density === "spacious") return "p-5"
  return "p-4"
}
