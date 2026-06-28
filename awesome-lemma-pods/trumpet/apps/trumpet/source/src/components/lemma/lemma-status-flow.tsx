import * as React from "react"
import {
  Check,
  Circle,
  Loader2,
} from "lucide-react"
import { Skeleton } from "@/components/lemma/ui/skeleton"
import {
  Tooltip,
  TooltipContent,
  TooltipProvider,
  TooltipTrigger,
} from "@/components/lemma/ui/tooltip"
import { useFunctionRun, useUpdateRecord } from "lemma-sdk/react"
import type { LemmaClient } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import { enumPillClasses, type EnumColorMap } from "./status-flow-enum-utils"
import {
  statusFlowRadiusClassName,
  type LemmaStatusFlowAppearance,
  type LemmaStatusFlowDensity,
  type LemmaStatusFlowRadius,
} from "./status-flow-style-utils"

export type { LemmaStatusFlowAppearance, LemmaStatusFlowDensity, LemmaStatusFlowRadius } from "./status-flow-style-utils"
export type { EnumColorMap } from "./status-flow-enum-utils"

export type LemmaStatusFlowMode = "interactive" | "readonly" | "tracker"

export interface StatusTransition {
  from: string
  to: string
  label?: string
  icon?: React.ComponentType<{ className?: string }>
  functionName?: string
}

export interface LemmaStatusFlowProps {
  client: LemmaClient
  podId?: string
  tableName?: string
  recordId?: string
  enabled?: boolean

  currentStatus: string
  statuses: string[]
  statusField?: string
  transitions?: StatusTransition[]
  onTransition?: (from: string, to: string) => void
  actionMode?: "direct" | "function"
  enumColorMap?: EnumColorMap
  mode?: LemmaStatusFlowMode
  showConnectors?: boolean
  statusLabels?: Record<string, React.ReactNode>
  statusDescriptions?: Record<string, React.ReactNode>

  orientation?: "horizontal" | "vertical"
  appearance?: LemmaStatusFlowAppearance
  density?: LemmaStatusFlowDensity
  radius?: LemmaStatusFlowRadius
  className?: string
}

type StatusNodeState = "past" | "current" | "future"

export function LemmaStatusFlow({
  client,
  podId,
  tableName,
  recordId,
  enabled = true,
  currentStatus,
  statuses,
  statusField = "status",
  transitions,
  onTransition,
  actionMode = "direct",
  enumColorMap,
  mode = "interactive",
  showConnectors = true,
  statusLabels,
  statusDescriptions,
  orientation = "horizontal",
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  className,
}: LemmaStatusFlowProps) {
  const [localStatus, setLocalStatus] = React.useState(currentStatus)
  const [transitioning, setTransitioning] = React.useState<string | null>(null)

  const isInteractive = enabled && mode === "interactive"
  const isTracker = mode === "tracker"
  const isHorizontal = orientation === "horizontal"

  React.useEffect(() => {
    setLocalStatus(currentStatus)
    setTransitioning(null)
  }, [currentStatus, recordId, tableName])

  const updateRecord = useUpdateRecord({
    client,
    podId,
    tableName: tableName ?? "",
    recordId: recordId ?? null,
    enabled: isInteractive && !!tableName && !!recordId,
  })

  const functionRun = useFunctionRun({
    client,
    podId,
  })

  if (!statuses || statuses.length === 0) {
    return null
  }

  const normalizedCurrentStatus = normalizeStatusKey(localStatus)
  const normalizedStatuses = React.useMemo(
    () => statuses.map((status) => normalizeStatusKey(status)),
    [statuses],
  )
  const currentIndex = normalizedStatuses.indexOf(normalizedCurrentStatus)

  const allowedTransitions = React.useMemo(() => {
    if (!transitions || transitions.length === 0) return null
    const map = new Map<string, StatusTransition[]>()
    for (const transition of transitions) {
      const list = map.get(normalizeStatusKey(transition.from)) ?? []
      list.push(transition)
      map.set(normalizeStatusKey(transition.from), list)
    }
    return map
  }, [transitions])

  const getNodeState = React.useCallback((status: string, index: number): StatusNodeState => {
    if (currentIndex < 0) {
      return normalizeStatusKey(status) === normalizedCurrentStatus ? "current" : "future"
    }
    if (index < currentIndex) return "past"
    if (index === currentIndex) return "current"
    return "future"
  }, [currentIndex, normalizedCurrentStatus])

  const isTransitionAllowed = React.useCallback((targetStatus: string): boolean => {
    if (!isInteractive) return false

    const normalizedTargetStatus = normalizeStatusKey(targetStatus)
    if (!allowedTransitions) {
      if (currentIndex < 0) return false
      const targetIdx = normalizedStatuses.indexOf(normalizedTargetStatus)
      return targetIdx === currentIndex + 1
    }

    const fromTransitions = allowedTransitions.get(normalizedCurrentStatus)
    if (!fromTransitions) return false
    return fromTransitions.some((transition) => normalizeStatusKey(transition.to) === normalizedTargetStatus)
  }, [allowedTransitions, currentIndex, isInteractive, normalizedCurrentStatus, normalizedStatuses])

  const getTransitionFor = React.useCallback((targetStatus: string): StatusTransition | undefined => {
    if (!isInteractive) return undefined

    const normalizedTargetStatus = normalizeStatusKey(targetStatus)
    if (!allowedTransitions) {
      if (currentIndex >= 0 && normalizedStatuses.indexOf(normalizedTargetStatus) === currentIndex + 1) {
        return { from: localStatus, to: targetStatus }
      }
      return undefined
    }

    return allowedTransitions
      .get(normalizedCurrentStatus)
      ?.find((transition) => normalizeStatusKey(transition.to) === normalizedTargetStatus)
  }, [allowedTransitions, currentIndex, isInteractive, localStatus, normalizedCurrentStatus, normalizedStatuses])

  const handleTransition = React.useCallback(async (targetStatus: string) => {
    const transition = getTransitionFor(targetStatus)
    if (!transition) return

    const nextStatus = transition.to ?? targetStatus

    setTransitioning(normalizeStatusKey(targetStatus))
    try {
      if (actionMode === "function" && transition.functionName) {
        await functionRun.start({
          id: recordId,
          record_id: recordId,
          from: localStatus,
          to: nextStatus,
          [statusField]: nextStatus,
        }, { functionName: transition.functionName })
      } else if (actionMode === "direct" && tableName && recordId) {
        await updateRecord.update({ [statusField]: nextStatus })
      }
      setLocalStatus(nextStatus)
      onTransition?.(localStatus, nextStatus)
    } finally {
      setTransitioning(null)
    }
  }, [actionMode, functionRun, getTransitionFor, localStatus, onTransition, recordId, statusField, tableName, updateRecord])

  const nodeSize = density === "compact" ? "h-6 min-w-6 px-2 text-[11px]" : density === "spacious" ? "h-8 min-w-8 px-3 text-xs" : "h-7 min-w-7 px-2.5 text-xs"
  const connectorGap = density === "compact" ? "gap-1" : density === "spacious" ? "gap-2.5" : "gap-1.5"
  const trackerNodeSize = density === "compact" ? "size-6" : density === "spacious" ? "size-9" : "size-7"
  const trackerIconSize = density === "compact" ? "size-3" : density === "spacious" ? "size-4" : "size-3.5"
  const trackerLabelSize = density === "compact" ? "text-[11px]" : density === "spacious" ? "text-sm" : "text-xs"
  const trackerDescriptionSize = density === "compact" ? "text-[10px]" : density === "spacious" ? "text-xs" : "text-[10px]"
  const isLoading = isInteractive && (updateRecord.isSubmitting || (actionMode === "function" && functionRun.isPolling))

  const renderPillNode = (status: string, index: number) => {
    const state = getNodeState(status, index)
    const canTransition = isTransitionAllowed(status)
    const isTransitioningTo = transitioning === normalizeStatusKey(status)
    const pillBase = enumPillClasses(status, statuses, enumColorMap)
    const displayLabel = resolveStatusNode(statusLabels, status) ?? status
    const Icon = canTransition ? getTransitionFor(status)?.icon : undefined

    const nodeContent = (
      <span
        className={cn(
          "inline-flex items-center justify-center gap-1 font-medium transition-all",
          statusFlowRadiusClassName(radius, "pill"),
          nodeSize,
          state === "past" && "bg-muted/60 text-muted-foreground line-through decoration-muted-foreground/40",
          state === "current" && [
            pillBase,
            "ring-2 ring-primary/30 ring-offset-1 ring-offset-background font-semibold",
          ],
          state === "future" && "border border-dashed border-border/60 bg-transparent text-muted-foreground",
          canTransition && "cursor-pointer hover:opacity-80 hover:ring-2 hover:ring-primary/20 hover:ring-offset-1",
          isTransitioningTo && "opacity-70",
        )}
        onClick={canTransition && !isTransitioningTo ? () => void handleTransition(status) : undefined}
        role={canTransition ? "button" : undefined}
        tabIndex={canTransition ? 0 : undefined}
        onKeyDown={canTransition ? (event: React.KeyboardEvent) => {
          if (event.key === "Enter" || event.key === " ") {
            event.preventDefault()
            void handleTransition(status)
          }
        } : undefined}
      >
        {state === "past" && <Check className="size-3 shrink-0" />}
        {state === "current" && <Circle className="size-2.5 shrink-0 fill-current" />}
        {isTransitioningTo && <Loader2 className="size-3 shrink-0 animate-spin" />}
        {!isTransitioningTo && Icon && <Icon className="size-3 shrink-0" />}
        <span className="truncate">{displayLabel}</span>
      </span>
    )

    const transition = getTransitionFor(status)

    if (canTransition && transition?.label) {
      return (
        <Tooltip key={status}>
          <TooltipTrigger>{nodeContent}</TooltipTrigger>
          <TooltipContent side={isHorizontal ? "top" : "right"} className="text-xs">
            {transition.label}
          </TooltipContent>
        </Tooltip>
      )
    }

    return <React.Fragment key={status}>{nodeContent}</React.Fragment>
  }

  const renderPillConnector = (fromIndex: number) => {
    const fromState = getNodeState(statuses[fromIndex], fromIndex)
    const toState = getNodeState(statuses[fromIndex + 1], fromIndex + 1)

    const isPastToCurrent = fromState === "past" && toState === "current"
    const isCurrentToFuture = fromState === "current" && toState === "future"
    const isPastToPast = fromState === "past" && toState === "past"

    const connectorClass = cn(
      isHorizontal ? "h-px min-w-4 flex-1" : "w-px min-h-4 flex-1",
      isPastToPast && "bg-emerald-400/60 dark:bg-emerald-500/30",
      isPastToCurrent && "bg-emerald-400/60 dark:bg-emerald-500/30",
      isCurrentToFuture && "border-dashed border-border/40 bg-transparent",
      !(isPastToPast || isPastToCurrent || isCurrentToFuture) && "bg-border/30",
    )

    const activeDot = isPastToCurrent && (
      <span
        className={cn(
          "size-1.5 shrink-0 rounded-full bg-primary",
          statusFlowRadiusClassName(radius, "pill"),
        )}
      />
    )

    if (isCurrentToFuture) {
      return (
        <span
          key={`connector-${fromIndex}`}
          className={cn(
            "border-dashed",
            isHorizontal ? "min-w-4 flex-1 border-t border-border/40" : "min-h-4 flex-1 border-l border-border/40",
          )}
        />
      )
    }

    return (
      <React.Fragment key={`connector-${fromIndex}`}>
        {activeDot}
        <span className={connectorClass} />
      </React.Fragment>
    )
  }

  const renderTrackerNode = (status: string, index: number) => {
    const state = getNodeState(status, index)
    const label = resolveStatusNode(statusLabels, status) ?? status
    const description = resolveStatusNode(statusDescriptions, status)

    return (
      <div
        key={status}
        className={cn(
          "flex",
          isHorizontal ? "flex-col items-center" : "flex-row items-center",
        )}
      >
        <span
          className={cn(
            "relative flex items-center justify-center border-2 transition-colors",
            trackerNodeSize,
            statusFlowRadiusClassName(radius, "pill"),
            trackerNodeBorderClass(state),
            trackerNodeBackgroundClass(state),
          )}
        >
          <span className="relative z-10">{trackerStatusIcon(state, trackerIconSize)}</span>
        </span>
        {(label || description) ? (
          <div className={cn("min-w-0", isHorizontal ? "mt-1.5 text-center" : "ml-3")}>
            {label ? (
              <p className={cn("truncate font-medium leading-tight", trackerLabelSize, trackerLabelColorClass(state))}>
                {label}
              </p>
            ) : null}
            {description ? (
              <div className={cn("truncate leading-tight text-muted-foreground", trackerDescriptionSize)}>
                {description}
              </div>
            ) : null}
          </div>
        ) : null}
      </div>
    )
  }

  const renderTrackerConnector = (fromIndex: number) => {
    const fromState = getNodeState(statuses[fromIndex], fromIndex)

    return (
      <div
        key={`tracker-connector-${fromIndex}`}
        className={cn(
          isHorizontal ? "flex items-center self-center" : "flex flex-col items-center",
          isHorizontal
            ? density === "compact" ? "h-0.5 w-8" : density === "spacious" ? "h-0.5 w-16" : "h-0.5 w-12"
            : density === "compact" ? "h-5 w-0.5" : density === "spacious" ? "h-10 w-0.5" : "h-7 w-0.5",
          trackerConnectorClass(fromState, orientation),
        )}
      />
    )
  }

  return (
    <TooltipProvider delay={300}>
      <div
        data-appearance={appearance}
        data-density={density}
        data-radius={radius}
        data-mode={mode}
        className={cn(
          "lemma-status-flow",
          isTracker
            ? isHorizontal ? "flex items-start" : "flex flex-col"
            : isHorizontal ? "flex flex-row items-center" : "flex flex-col items-start",
          !isTracker && connectorGap,
          statusFlowRootClassName(appearance),
          density === "compact" ? "p-1.5" : density === "spacious" ? "p-3" : "p-2",
          className,
        )}
      >
        {isLoading ? <Loader2 className="size-3 shrink-0 animate-spin text-muted-foreground" /> : null}
        {statuses.map((status, index) => (
          <React.Fragment key={status}>
            {isTracker ? renderTrackerNode(status, index) : renderPillNode(status, index)}
            {showConnectors && index < statuses.length - 1 ? (isTracker ? renderTrackerConnector(index) : renderPillConnector(index)) : null}
          </React.Fragment>
        ))}
      </div>
    </TooltipProvider>
  )
}

function normalizeStatusKey(value: string | null | undefined): string {
  return String(value ?? "").trim().toLowerCase()
}

function resolveStatusNode<T>(map: Record<string, T> | undefined, status: string): T | undefined {
  if (!map) return undefined
  return map[status] ?? map[normalizeStatusKey(status)]
}

function trackerStatusIcon(state: StatusNodeState, iconSize: string): React.ReactNode {
  if (state === "past") return <Check className={cn("text-white", iconSize)} />
  if (state === "current") return <Circle className={cn("fill-current text-white", iconSize)} />
  return <Circle className={cn("text-muted-foreground/40", iconSize)} />
}

function trackerNodeBorderClass(state: StatusNodeState): string {
  if (state === "past") return "border-emerald-500 dark:border-emerald-400"
  if (state === "current") return "border-primary"
  return "border-dashed border-muted-foreground/30"
}

function trackerNodeBackgroundClass(state: StatusNodeState): string {
  if (state === "past") return "bg-emerald-500 dark:bg-emerald-400"
  if (state === "current") return "bg-primary text-primary-foreground"
  return "bg-transparent"
}

function trackerLabelColorClass(state: StatusNodeState): string {
  if (state === "past") return "text-emerald-600 dark:text-emerald-400"
  if (state === "current") return "text-foreground"
  return "text-muted-foreground"
}

function trackerConnectorClass(
  fromState: StatusNodeState,
  orientation: "horizontal" | "vertical",
): string {
  if (fromState === "past") return "bg-emerald-400 dark:bg-emerald-500/40"
  if (fromState === "current") {
    return orientation === "horizontal" ? "border-t border-dashed border-primary/40 bg-transparent" : "border-l border-dashed border-primary/40 bg-transparent"
  }
  return orientation === "horizontal" ? "border-t border-dashed border-muted-foreground/20 bg-transparent" : "border-l border-dashed border-muted-foreground/20 bg-transparent"
}

export function LemmaStatusFlowSkeleton({
  count = 4,
  orientation = "horizontal",
  density = "comfortable",
  className,
}: {
  count?: number
  orientation?: "horizontal" | "vertical"
  density?: LemmaStatusFlowDensity
  className?: string
}) {
  const isHorizontal = orientation === "horizontal"
  const pillH = density === "compact" ? "h-5" : density === "spacious" ? "h-7" : "h-6"
  const pillW = density === "compact" ? "w-16" : density === "spacious" ? "w-24" : "w-20"

  return (
    <div
      className={cn(
        isHorizontal ? "flex flex-row items-center gap-2" : "flex flex-col items-start gap-2",
        density === "compact" ? "p-1.5" : density === "spacious" ? "p-3" : "p-2",
        className,
      )}
    >
      {Array.from({ length: count }).map((_, index) => (
        <React.Fragment key={index}>
          <Skeleton className={cn(pillH, pillW, "rounded-full")} />
          {index < count - 1 ? <Skeleton className={cn(isHorizontal ? "h-px w-4" : "h-4 w-px")} /> : null}
        </React.Fragment>
      ))}
    </div>
  )
}

function statusFlowRootClassName(appearance: LemmaStatusFlowAppearance) {
  if (appearance === "contained") return "border border-border/30 bg-card"
  if (appearance === "minimal" || appearance === "borderless") return "bg-transparent"
  return "bg-background"
}
