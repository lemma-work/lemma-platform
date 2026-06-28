import * as React from "react"
import {
  Zap,
  CheckCircle2,
  XCircle,
  Clock,
  Play,
  RefreshCw,
  ChevronRight,
} from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import { Skeleton } from "@/components/lemma/ui/skeleton"
import { useWorkflowRuns, useWorkflowRunWaitAssignments } from "lemma-sdk/react"
import type { FlowRun, LemmaClient, WorkflowRunWait } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import {
  runStatusClasses,
  stepStatusClasses,
  toRunStatus,
  toStepStatus,
  type EnumColorMap,
  type WorkflowRunStatus,
  type WorkflowStepStatus,
} from "./workflow-enum-utils"
import {
  workflowRadiusClassName,
  type LemmaWorkflowAppearance,
  type LemmaWorkflowDensity,
  type LemmaWorkflowRadius,
} from "./workflow-style-utils"

export type {
  LemmaWorkflowAppearance,
  LemmaWorkflowDensity,
  LemmaWorkflowRadius,
} from "./workflow-style-utils"
export type {
  EnumColorMap,
  WorkflowRunStatus,
  WorkflowStepStatus,
} from "./workflow-enum-utils"

export interface WorkflowStep {
  name: string
  status: WorkflowStepStatus
  started_at?: string
  completed_at?: string
  node_id?: string
  error?: string | null
  [key: string]: unknown
}

export interface LemmaWorkflowRunnerProps {
  client: LemmaClient
  podId?: string
  enabled?: boolean
  workflowName?: string
  runId?: string
  enumColorMap?: EnumColorMap

  appearance?: LemmaWorkflowAppearance
  density?: LemmaWorkflowDensity
  radius?: LemmaWorkflowRadius

  onRunClick?: (run: FlowRun) => void
  onManualStart?: () => void
  showAssignedToMe?: boolean
  title?: React.ReactNode
  headerActions?: React.ReactNode
  className?: string

  tableName?: string
  stepsTable?: string
  stepsForeignKey?: string
  stepsField?: string
}

export function LemmaWorkflowRunner({
  client,
  podId,
  enabled = true,
  workflowName,
  runId: initialRunId,
  enumColorMap,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  onRunClick,
  onManualStart,
  showAssignedToMe = true,
  title,
  headerActions,
  className,
}: LemmaWorkflowRunnerProps) {
  const [selectedRunId, setSelectedRunId] = React.useState<string | null>(
    initialRunId ?? null,
  )
  const [standaloneRun, setStandaloneRun] = React.useState<FlowRun | null>(null)
  const [isStandaloneLoading, setIsStandaloneLoading] = React.useState(false)
  const [standaloneError, setStandaloneError] = React.useState<Error | null>(null)

  const trimmedWorkflowName = workflowName?.trim() ?? ""
  const scopedClient = React.useMemo(
    () => (podId && podId !== client.podId ? client.withPod(podId) : client),
    [client, podId],
  )

  const runsState = useWorkflowRuns({
    client,
    podId,
    workflowName: trimmedWorkflowName,
    enabled: enabled && trimmedWorkflowName.length > 0,
    initialRunId: initialRunId ?? null,
  })
  const waitingState = useWorkflowRunWaitAssignments({
    client,
    podId,
    enabled: enabled && showAssignedToMe,
  })

  React.useEffect(() => {
    setSelectedRunId(initialRunId ?? null)
  }, [initialRunId])

  const refreshStandaloneRun = React.useCallback(async () => {
    if (!enabled || !selectedRunId) {
      setStandaloneRun(null)
      setStandaloneError(null)
      setIsStandaloneLoading(false)
      return null
    }

    setIsStandaloneLoading(true)
    setStandaloneError(null)

    try {
      const run = await scopedClient.workflows.runs.get(selectedRunId)
      setStandaloneRun(run)
      return run
    } catch (error) {
      const normalized =
        error instanceof Error
          ? error
          : new Error("Failed to load workflow run.")
      setStandaloneRun(null)
      setStandaloneError(normalized)
      return null
    } finally {
      setIsStandaloneLoading(false)
    }
  }, [enabled, scopedClient, selectedRunId])

  React.useEffect(() => {
    if (!enabled || !selectedRunId) {
      setStandaloneRun(null)
      setStandaloneError(null)
      setIsStandaloneLoading(false)
      return
    }

    if (runsState.runs.some((run) => run.id === selectedRunId)) {
      setStandaloneRun(null)
      setStandaloneError(null)
      setIsStandaloneLoading(false)
      return
    }

    let cancelled = false

    void (async () => {
      setIsStandaloneLoading(true)
      setStandaloneError(null)

      try {
        const run = await scopedClient.workflows.runs.get(selectedRunId)
        if (cancelled) return
        setStandaloneRun(run)
      } catch (error) {
        if (cancelled) return
        const normalized =
          error instanceof Error
            ? error
            : new Error("Failed to load workflow run.")
        setStandaloneRun(null)
        setStandaloneError(normalized)
      } finally {
        if (!cancelled) setIsStandaloneLoading(false)
      }
    })()

    return () => {
      cancelled = true
    }
  }, [enabled, runsState.runs, scopedClient, selectedRunId])

  const runs = React.useMemo(() => {
    const nextRuns = [...runsState.runs]
    if (standaloneRun?.id && !nextRuns.some((run) => run.id === standaloneRun.id)) {
      nextRuns.unshift(standaloneRun)
    }
    return nextRuns
  }, [runsState.runs, standaloneRun])

  const effectiveSelectedRunId =
    selectedRunId ?? runsState.effectiveSelectedRunId ?? standaloneRun?.id ?? null
  const selectedRun = React.useMemo(
    () =>
      effectiveSelectedRunId
        ? runs.find((run) => run.id === effectiveSelectedRunId) ?? null
        : null,
    [effectiveSelectedRunId, runs],
  )

  const parsedSteps = React.useMemo<WorkflowStep[]>(() => {
    if (!selectedRun) return []

    if (Array.isArray(selectedRun.step_history) && selectedRun.step_history.length > 0) {
      return selectedRun.step_history.map(
        (step): WorkflowStep => ({
          name: String(step.node_id ?? "Step"),
          node_id: step.node_id,
          status: toStepStatus(step.status),
          started_at: step.started_at ? String(step.started_at) : undefined,
          completed_at: step.completed_at ? String(step.completed_at) : undefined,
          error: step.error ?? null,
        }),
      )
    }

    return normalizeStepsFromContext(selectedRun.execution_context)
  }, [selectedRun])

  const completedSteps = parsedSteps.filter(
    (step) => step.status === "completed",
  ).length
  const totalSteps = parsedSteps.length
  const progressPct =
    totalSteps > 0 ? Math.round((completedSteps / totalSteps) * 100) : 0

  const isLoading =
    trimmedWorkflowName.length > 0 ? runsState.isLoading : isStandaloneLoading
  const hasError =
    trimmedWorkflowName.length > 0 ? runsState.error : standaloneError

  const handleRefresh = React.useCallback(() => {
    if (showAssignedToMe) {
      void waitingState.refresh()
    }
    if (trimmedWorkflowName.length > 0) {
      void runsState.refresh()
      if (
        selectedRunId &&
        !runsState.runs.some((run) => run.id === selectedRunId)
      ) {
        void refreshStandaloneRun()
      }
      return
    }

    void refreshStandaloneRun()
  }, [
    refreshStandaloneRun,
    runsState,
    selectedRunId,
    showAssignedToMe,
    trimmedWorkflowName.length,
    waitingState,
  ])

  return (
    <div
      data-appearance={appearance}
      data-density={density}
      data-radius={radius}
      className={cn(
        "lemma-workflow-runner flex h-full min-h-0 flex-col",
        workflowRootClassName(appearance),
        className,
      )}
    >
      <div className={cn("shrink-0", workflowHeaderClassName(appearance))}>
        <div
          className={cn(
            "flex items-center justify-between",
            workflowToolbarClassName(density),
          )}
        >
          <div className="min-w-0 flex items-center gap-3">
            <span
              className={cn(
                "flex size-7 items-center justify-center border border-border/50 bg-muted/40 text-muted-foreground",
                workflowRadiusClassName(radius, "control"),
              )}
            >
              <Zap className="size-3.5" />
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold text-foreground">
                {title ?? (trimmedWorkflowName || "Workflow runs")}
              </h1>
              <p className="text-xs text-muted-foreground">
                {runs.length} run{runs.length !== 1 ? "s" : ""}
                {showAssignedToMe && waitingState.assignments.length > 0
                  ? ` · ${waitingState.assignments.length} waiting`
                  : ""}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {headerActions}
            {onManualStart ? (
              <Button size="sm" onClick={onManualStart} className="h-8 gap-2 text-xs">
                <Play className="size-3.5" />
                Start
              </Button>
            ) : null}
          </div>
        </div>
      </div>

      <div
        className={cn(
          "flex flex-1 min-h-0 overflow-hidden",
          workflowContentClassName(density),
        )}
      >
        {hasError ? (
          <div className="flex min-h-48 flex-1 flex-col items-center justify-center gap-3 text-center">
            <p className="text-sm text-destructive">{hasError.message}</p>
            <Button variant="outline" size="sm" onClick={handleRefresh}>
              <RefreshCw className="mr-2 size-3.5" />
              Retry
            </Button>
          </div>
        ) : isLoading ? (
          <div className="flex-1 space-y-3">
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="flex items-center gap-3">
                <Skeleton className="size-8 shrink-0 rounded-full" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-3/4" />
                  <Skeleton className="h-3 w-1/3" />
                </div>
              </div>
            ))}
          </div>
        ) : runs.length === 0 && waitingState.assignments.length === 0 ? (
          <div className="flex min-h-48 flex-1 flex-col items-center justify-center gap-3 text-center">
            <div
              className={cn(
                "flex size-10 items-center justify-center border border-border/60 bg-muted/40 text-muted-foreground",
                workflowRadiusClassName(radius, "pill"),
              )}
            >
              <Zap className="size-5" />
            </div>
            <div>
              <p className="font-medium text-foreground">No workflow runs</p>
              <p className="mt-1 text-sm text-muted-foreground">
                {trimmedWorkflowName || initialRunId
                  ? "Runs will appear here when the workflow starts."
                  : "Provide a workflow name or run id to inspect native workflow runs."}
              </p>
            </div>
          </div>
        ) : (
          <>
            <div
              className={cn(
                "shrink-0 flex flex-col overflow-auto border-r border-border/30",
                density === "compact"
                  ? "w-56"
                  : density === "spacious"
                    ? "w-80"
                    : "w-64",
              )}
            >
              <div
                className={cn(
                  "flex-1 overflow-auto",
                  density === "compact"
                    ? "py-1"
                    : density === "spacious"
                      ? "py-3"
                      : "py-2",
                )}
              >
                {showAssignedToMe && waitingState.assignments.length > 0 ? (
                  <div className={density === "compact" ? "px-2 pb-1" : "px-3 pb-2"}>
                    <p className="mb-1 px-1 text-[10px] font-semibold uppercase tracking-wider text-muted-foreground">
                      Waiting for me
                    </p>
                    <div className="space-y-1">
                      {waitingState.assignments.map((assignment) => {
                        const run = assignment.run
                        const runIdentifier = run.id ? String(run.id) : assignment.wait.id ?? assignment.wait.node_id
                        const isActive = run.id === effectiveSelectedRunId

                        return (
                          <button
                            key={`wait:${runIdentifier}`}
                            type="button"
                            onClick={() => {
                              setSelectedRunId(run.id ?? null)
                              onRunClick?.(run)
                            }}
                            className={cn(
                              "flex w-full items-center gap-3 text-left transition-colors",
                              isActive ? "bg-amber-500/10" : "hover:bg-amber-500/5",
                              density === "compact"
                                ? "px-2 py-1.5"
                                : density === "spacious"
                                  ? "px-4 py-3"
                                  : "px-3 py-2",
                              workflowRadiusClassName(radius, "control"),
                            )}
                          >
                            <span className="flex size-7 items-center justify-center rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400">
                              <Clock className="size-3.5" />
                            </span>
                            <div className="min-w-0 flex-1">
                              <p className="truncate text-sm font-medium text-foreground">
                                {labelForWait(assignment, trimmedWorkflowName)}
                              </p>
                              <div className="mt-0.5 flex items-center gap-2">
                                <span className={stepStatusClasses("waiting")}>waiting</span>
                                {assignment.wait.created_at ? (
                                  <span className="text-[10px] text-muted-foreground">
                                    {formatTimestamp(assignment.wait.created_at)}
                                  </span>
                                ) : null}
                              </div>
                            </div>
                            <ChevronRight
                              className={cn(
                                "size-3.5 shrink-0 text-muted-foreground",
                                isActive && "text-foreground",
                              )}
                            />
                          </button>
                        )
                      })}
                    </div>
                  </div>
                ) : null}

                {runs.map((run) => {
                  const status = toRunStatus(run.status)
                  const runIdentifier = run.id ? String(run.id) : "current"
                  const isActive = run.id === effectiveSelectedRunId

                  return (
                    <button
                      key={runIdentifier}
                      type="button"
                      onClick={() => {
                        setSelectedRunId(run.id ?? null)
                        onRunClick?.(run)
                      }}
                      className={cn(
                        "flex w-full items-center gap-3 text-left transition-colors",
                        isActive ? "bg-muted/50" : "hover:bg-muted/20",
                        density === "compact"
                          ? "px-2 py-1.5"
                          : density === "spacious"
                            ? "px-4 py-3"
                            : "px-3 py-2",
                        workflowRadiusClassName(radius, "control"),
                      )}
                    >
                      <span className={runStatusIconWrapper(status)}>
                        {runStatusIcon(status)}
                      </span>
                      <div className="min-w-0 flex-1">
                        <p className="truncate text-sm font-medium text-foreground">
                          {labelForRun(run, trimmedWorkflowName)}
                        </p>
                        <div className="mt-0.5 flex items-center gap-2">
                          <span className={runStatusClasses(status)}>{status}</span>
                          {run.started_at ? (
                            <span className="text-[10px] text-muted-foreground">
                              {formatTimestamp(run.started_at)}
                            </span>
                          ) : null}
                        </div>
                      </div>
                      <ChevronRight
                        className={cn(
                          "size-3.5 shrink-0 text-muted-foreground",
                          isActive && "text-foreground",
                        )}
                      />
                    </button>
                  )
                })}
              </div>
            </div>

            <div className="flex-1 overflow-auto">
              {!selectedRun ? (
                <div className="flex h-full min-h-48 flex-col items-center justify-center gap-3 text-center">
                  <Clock className="size-8 text-muted-foreground/40" />
                  <p className="text-sm text-muted-foreground">
                    Select a run to view details
                  </p>
                </div>
              ) : (
                <div
                  className={cn(
                    density === "compact"
                      ? "p-3"
                      : density === "spacious"
                        ? "p-5"
                        : "p-4",
                  )}
                >
                  <div className="mb-4">
                    <div className="flex items-center justify-between gap-2">
                      <h2 className="truncate text-base font-semibold text-foreground">
                        {labelForRun(selectedRun, trimmedWorkflowName)}
                      </h2>
                      <span className={runStatusClasses(toRunStatus(selectedRun.status))}>
                        {toRunStatus(selectedRun.status)}
                      </span>
                    </div>
                    <div className="mt-2 flex flex-wrap items-center gap-3 text-xs text-muted-foreground">
                      {selectedRun.started_at ? (
                        <span className="inline-flex items-center gap-1">
                          <Clock className="size-3" />
                          Started: {formatTimestamp(selectedRun.started_at)}
                        </span>
                      ) : null}
                      {selectedRun.completed_at ? (
                        <span className="inline-flex items-center gap-1">
                          <CheckCircle2 className="size-3" />
                          Completed: {formatTimestamp(selectedRun.completed_at)}
                        </span>
                      ) : null}
                      {selectedRun.id ? (
                        <span className="inline-flex items-center gap-1">
                          <span className="font-medium text-foreground/80">Run</span>
                          <span>{selectedRun.id}</span>
                        </span>
                      ) : null}
                    </div>
                  </div>

                  {parsedSteps.length > 0 ? (
                    <div>
                      <div className="mb-3 flex items-center justify-between">
                        <span className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
                          Steps
                        </span>
                        <span className="text-xs text-muted-foreground">
                          {completedSteps}/{totalSteps}
                        </span>
                      </div>

                      {totalSteps > 0 ? (
                        <div className="mb-4 h-1.5 overflow-hidden rounded-full bg-muted/60">
                          <div
                            className={cn(
                              "h-full rounded-full transition-all",
                              progressPct === 100 ? "bg-emerald-500" : "bg-blue-500",
                            )}
                            style={{ width: `${progressPct}%` }}
                          />
                        </div>
                      ) : null}

                      <div
                        className={cn(
                          "relative flex flex-col",
                          density === "compact"
                            ? "gap-2"
                            : density === "spacious"
                              ? "gap-4"
                              : "gap-3",
                        )}
                      >
                        {parsedSteps.map((step, index) => (
                          <div
                            key={`${step.node_id ?? step.name}-${index}`}
                            className="grid grid-cols-[auto_minmax(0,1fr)] gap-3"
                          >
                            <div className="flex flex-col items-center">
                              <span
                                className={cn(
                                  "flex size-7 items-center justify-center border",
                                  stepIconBorder(step.status),
                                  stepIconBg(step.status),
                                  workflowRadiusClassName(radius, "pill"),
                                )}
                              >
                                {stepStatusIcon(step.status)}
                              </span>
                              {index < parsedSteps.length - 1 ? (
                                <span
                                  className={cn(
                                    "my-1 h-full min-h-4 w-px",
                                    stepConnectorClass(step.status),
                                  )}
                                />
                              ) : null}
                            </div>
                            <div
                              className={cn(
                                "border border-border/30 bg-muted/10",
                                workflowRadiusClassName(radius, "surface"),
                                density === "compact"
                                  ? "p-2"
                                  : density === "spacious"
                                    ? "p-4"
                                    : "p-3",
                              )}
                            >
                              <div className="flex items-center justify-between gap-2">
                                <p className="truncate text-sm font-medium text-foreground">
                                  {step.name}
                                </p>
                                <span className={stepStatusClasses(step.status)}>
                                  {step.status}
                                </span>
                              </div>
                              <div className="mt-1 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                                {step.started_at ? (
                                  <span className="inline-flex items-center gap-1">
                                    <Clock className="size-3" />
                                    {formatTimestamp(step.started_at)}
                                  </span>
                                ) : null}
                                {step.completed_at ? (
                                  <span className="inline-flex items-center gap-1">
                                    <CheckCircle2 className="size-3" />
                                    {formatTimestamp(step.completed_at)}
                                  </span>
                                ) : null}
                                {step.started_at && step.completed_at ? (
                                  <span>{formatDuration(step.started_at, step.completed_at)}</span>
                                ) : null}
                                {step.error ? (
                                  <span className="text-destructive">{step.error}</span>
                                ) : null}
                              </div>
                            </div>
                          </div>
                        ))}
                      </div>
                    </div>
                  ) : (
                    <div className="flex min-h-32 flex-col items-center justify-center gap-2 text-center">
                      <Clock className="size-6 text-muted-foreground/40" />
                      <p className="text-sm text-muted-foreground">
                        No step data available
                      </p>
                    </div>
                  )}
                </div>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function normalizeStepsFromContext(
  context: FlowRun["execution_context"] | null | undefined,
): WorkflowStep[] {
  const rawSteps = context?.steps
  if (!Array.isArray(rawSteps)) return []

  return rawSteps.map((step): WorkflowStep => {
    const item = step as Record<string, unknown>
    return {
      name: String(item.label ?? item.name ?? item.node_id ?? item.step ?? "Step"),
      node_id: item.node_id ? String(item.node_id) : undefined,
      status: toStepStatus(item.status),
      started_at: item.started_at ? String(item.started_at) : undefined,
      completed_at: item.completed_at ? String(item.completed_at) : undefined,
      error: item.error ? String(item.error) : null,
    }
  })
}

function labelForRun(run: FlowRun, workflowName?: string): string {
  if (run.id) {
    const suffix = run.id.length > 8 ? run.id.slice(0, 8) : run.id
    return `${workflowName || "Run"} ${suffix}`
  }
  return workflowName || "Workflow run"
}

function labelForWait(assignment: WorkflowRunWait, workflowName?: string): string {
  const node = assignment.wait.node_id
  const runId = assignment.run.id
  const suffix = runId ? (runId.length > 8 ? runId.slice(0, 8) : runId) : null
  if (node && suffix) return `${workflowName || "Workflow"} ${suffix} · ${node}`
  if (node) return `${workflowName || "Workflow"} · ${node}`
  return workflowName || "Waiting workflow run"
}

function runStatusIcon(status: WorkflowRunStatus): React.ReactNode {
  switch (status) {
    case "pending":
      return <Clock className="size-3.5" />
    case "running":
      return <RefreshCw className="size-3.5 animate-spin" />
    case "completed":
      return <CheckCircle2 className="size-3.5" />
    case "failed":
      return <XCircle className="size-3.5" />
  }
}

function runStatusIconWrapper(status: WorkflowRunStatus): string {
  switch (status) {
    case "pending":
      return "flex size-7 items-center justify-center rounded-full bg-amber-500/10 text-amber-600 dark:text-amber-400"
    case "running":
      return "flex size-7 items-center justify-center rounded-full bg-blue-500/10 text-blue-600 dark:text-blue-400"
    case "completed":
      return "flex size-7 items-center justify-center rounded-full bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
    case "failed":
      return "flex size-7 items-center justify-center rounded-full bg-red-500/10 text-red-600 dark:text-red-400"
  }
}

function stepStatusIcon(status: WorkflowStepStatus): React.ReactNode {
  switch (status) {
    case "waiting":
      return <Clock className="size-3.5" />
    case "active":
      return <RefreshCw className="size-3.5 animate-spin" />
    case "completed":
      return <CheckCircle2 className="size-3.5" />
    case "failed":
      return <XCircle className="size-3.5" />
  }
}

function stepIconBorder(status: WorkflowStepStatus): string {
  switch (status) {
    case "waiting":
      return "border-amber-200 dark:border-amber-500/30"
    case "active":
      return "border-blue-200 dark:border-blue-500/30"
    case "completed":
      return "border-emerald-200 dark:border-emerald-500/30"
    case "failed":
      return "border-red-200 dark:border-red-500/30"
  }
}

function stepIconBg(status: WorkflowStepStatus): string {
  switch (status) {
    case "waiting":
      return "bg-amber-500/10 text-amber-600 dark:text-amber-400"
    case "active":
      return "bg-blue-500/10 text-blue-600 dark:text-blue-400"
    case "completed":
      return "bg-emerald-500/10 text-emerald-600 dark:text-emerald-400"
    case "failed":
      return "bg-red-500/10 text-red-600 dark:text-red-400"
  }
}

function stepConnectorClass(status: WorkflowStepStatus): string {
  if (status === "completed") return "bg-emerald-300 dark:bg-emerald-500/30"
  if (status === "failed") return "bg-red-300 dark:bg-red-500/30"
  if (status === "active") return "bg-blue-300 dark:bg-blue-500/30"
  return "bg-border/40"
}

function formatTimestamp(value: unknown): string {
  const date = new Date(String(value))
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleDateString(undefined, {
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function formatDuration(start: string, end: string): string {
  const startedAt = new Date(start)
  const completedAt = new Date(end)
  if (
    Number.isNaN(startedAt.getTime()) ||
    Number.isNaN(completedAt.getTime())
  ) {
    return ""
  }

  const diffMs = completedAt.getTime() - startedAt.getTime()
  const seconds = Math.floor(diffMs / 1000)
  if (seconds < 60) return `${seconds}s`

  const minutes = Math.floor(seconds / 60)
  const remainingSeconds = seconds % 60
  if (minutes < 60) return `${minutes}m ${remainingSeconds}s`

  const hours = Math.floor(minutes / 60)
  const remainingMinutes = minutes % 60
  return `${hours}h ${remainingMinutes}m`
}

function workflowRootClassName(appearance: LemmaWorkflowAppearance) {
  if (appearance === "contained") return "bg-card"
  if (appearance === "minimal" || appearance === "borderless") {
    return "bg-transparent"
  }
  return "bg-background"
}

function workflowHeaderClassName(appearance: LemmaWorkflowAppearance) {
  if (appearance === "borderless") return "bg-transparent"
  if (appearance === "minimal") return "border-b border-border/15 bg-transparent"
  if (appearance === "contained") return "border-b border-border/60 bg-card"
  return "border-b border-border/40 bg-card/95"
}

function workflowToolbarClassName(density: LemmaWorkflowDensity) {
  if (density === "compact") return "gap-2 px-3 py-2"
  if (density === "spacious") return "gap-4 px-5 py-4"
  return "gap-3 px-4 py-3"
}

function workflowContentClassName(density: LemmaWorkflowDensity) {
  if (density === "compact") return "p-2"
  if (density === "spacious") return "p-5"
  return "p-4"
}
