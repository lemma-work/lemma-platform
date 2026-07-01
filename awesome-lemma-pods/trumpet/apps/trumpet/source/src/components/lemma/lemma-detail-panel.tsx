"use client"

import * as React from "react"
import { AlertCircle, FileText, Loader2, RefreshCw } from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import { Skeleton } from "@/components/lemma/ui/skeleton"
import { useRecord, useTable, useUpdateRecord } from "lemma-sdk/react"
import { type LemmaClient, type Table } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import {
  RecordDetail,
  type RecordDetailBuiltinTab,
  type RecordDetailCustomTab,
  type RecordDetailFieldGroup,
  type RecordDetailLayout,
  type RecordDetailMode,
  type RecordDetailRelatedRecord,
  type RecordDetailSectionVisibilityRule,
  type RecordDetailTabContext,
  type RecordDetailVariant,
} from "@/components/lemma/records-detail"
import {
  createRecordActionInput,
  resolveRecordActionMode,
  resolveRecordActionValues,
  type RecordAction,
  type RecordActionMode,
} from "@/components/lemma/records-quick-actions"
import { recordsRadiusClassName, type LemmaRecordsAppearance, type LemmaRecordsDensity, type LemmaRecordsRadius } from "@/components/lemma/records-style-utils"
import type { EnumColorMap } from "@/components/lemma/records-enum-utils"

export type LemmaDetailPanelAppearance = LemmaRecordsAppearance
export type LemmaDetailPanelDensity = LemmaRecordsDensity
export type LemmaDetailPanelRadius = LemmaRecordsRadius
export type FieldGroup = RecordDetailFieldGroup
export type DetailTab = RecordDetailCustomTab
type LegacyDetailTabId = "comments" | "activity" | "files"
type LegacyCompatibleDetailTab = RecordDetailBuiltinTab | LegacyDetailTabId

export type { EnumColorMap } from "@/components/lemma/records-enum-utils"
export type {
  RecordDetailBuiltinTab,
  RecordDetailLayout,
  RecordDetailMode,
  RecordDetailRelatedRecord,
  RecordDetailSectionVisibilityRule,
  RecordDetailVariant,
} from "@/components/lemma/records-detail"

export type DetailAction = RecordAction

export interface LemmaDetailPanelProps {
  client: LemmaClient
  podId?: string
  tableName: string
  recordId?: string
  enabled?: boolean
  mode?: RecordDetailMode
  variant?: RecordDetailVariant
  layout?: RecordDetailLayout
  headerFields?: string[]
  fieldGroups?: FieldGroup[]
  detailTabs?: LegacyCompatibleDetailTab[]
  tabs?: DetailTab[]
  relatedRecords?: RecordDetailRelatedRecord[]
  renderFiles?: (context: { record: Record<string, unknown>; table: Table; recordId: string }) => React.ReactNode
  renderComments?: (context: { record: Record<string, unknown>; table: Table; recordId: string }) => React.ReactNode
  renderActivity?: (context: { record: Record<string, unknown>; table: Table; recordId: string }) => React.ReactNode
  sectionLabels?: Partial<Record<string, React.ReactNode>>
  sectionVisibility?: Partial<Record<string, RecordDetailSectionVisibilityRule>>
  actions?: DetailAction[]
  actionMode?: RecordActionMode
  updateVia?: "direct" | "function"
  updateFunctionName?: string
  statusField?: string
  titleField?: string
  descriptionField?: string
  identifierField?: string
  foreignKeyLabels?: Record<string, string>
  enumColorMap?: EnumColorMap
  onNavigate?: (type: string, id: string) => void
  onRecordChange?: (record: Record<string, unknown>) => void
  appearance?: LemmaDetailPanelAppearance
  density?: LemmaDetailPanelDensity
  radius?: LemmaDetailPanelRadius
  tableLabel?: React.ReactNode
  title?: React.ReactNode
  className?: string
}

export function LemmaDetailPanel({
  client,
  podId,
  tableName,
  recordId,
  enabled = true,
  mode = "view",
  variant = "summary",
  layout = "embedded",
  headerFields,
  fieldGroups,
  detailTabs,
  tabs,
  relatedRecords = [],
  renderFiles,
  renderComments,
  renderActivity,
  sectionLabels,
  sectionVisibility,
  actions,
  actionMode,
  updateVia,
  updateFunctionName,
  statusField,
  titleField,
  descriptionField,
  identifierField,
  foreignKeyLabels,
  enumColorMap,
  onNavigate,
  onRecordChange,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  tableLabel,
  title,
  className,
}: LemmaDetailPanelProps) {
  const tableState = useTable({ client, podId, tableName, enabled })
  const recordState = useRecord({
    client,
    podId,
    tableName,
    recordId: recordId ?? null,
    enabled: enabled && !!recordId,
  })

  const table = tableState.table
  const record = recordState.record
  const error = tableState.error ?? recordState.error

  const scopedClient = React.useMemo(
    () => (podId ? client.withPod(podId) : client),
    [client, podId],
  )

  const updateHook = useUpdateRecord({
    client,
    podId,
    tableName,
    recordId: recordId ?? null,
    enabled: enabled && !!recordId,
    updateVia,
    updateFunctionName,
  })

  const resolvedTabs = React.useMemo(() => {
    return resolveLegacyDetailTabs({
      detailTabs,
      tabs,
      renderFiles,
      renderComments,
      renderActivity,
    })
  }, [detailTabs, renderActivity, renderComments, renderFiles, tabs])

  const [actionLoading, setActionLoading] = React.useState<string | null>(null)

  const refreshRecord = React.useCallback(async () => {
    const refreshed = await recordState.refresh()
    if (refreshed) {
      onRecordChange?.(refreshed)
    }
  }, [onRecordChange, recordState])

  const handleAction = React.useCallback(
    async (action: DetailAction) => {
      if (!record || record.id == null) return

      const recordIdValue = String(record.id)
      const context = { tableName, scope: "detail" as const, record, recordId: recordIdValue }
      const mode = resolveRecordActionMode(action, actionMode)

      setActionLoading(action.label)
      try {
        if (mode === "function") {
          const functionName = action.functionName ?? tableName
          await scopedClient.functions.runs.create(functionName, { input: createRecordActionInput(action, context) })
        } else if (mode === "workflow") {
          if (!action.workflowName) throw new Error(`Action "${action.label}" requires workflowName in workflow mode.`)
          await scopedClient.workflows.runs.start(action.workflowName, createRecordActionInput(action, context))
        } else {
          await updateHook.update({
            ...resolveRecordActionValues(action, record),
            ...(action.buildInput?.(record, context) ?? {}),
          })
        }

        await refreshRecord()
      } finally {
        setActionLoading(null)
      }
    },
    [actionMode, record, refreshRecord, scopedClient, tableName, updateHook],
  )

  React.useEffect(() => {
    if (record) {
      onRecordChange?.(record)
    }
  }, [record, onRecordChange])

  if (!recordId) {
    return (
      <DetailPanelState
        appearance={appearance}
        radius={radius}
        className={className}
        icon={FileText}
        title="Select a record"
        description="Select a record to view details."
      />
    )
  }

  if (tableState.isLoading || recordState.isLoading) {
    return (
      <div
        data-appearance={appearance}
        data-density={density}
        data-radius={radius}
        className={detailPanelSurfaceClassName(appearance, radius, className)}
      >
        <div className={cn("border-b border-border/40", density === "compact" ? "px-4 py-3" : density === "spacious" ? "px-6 py-5" : "px-5 py-4")}>
          <div className="flex items-center gap-2">
            <Skeleton className="h-5 w-20" />
            <Skeleton className="h-5 w-16" />
          </div>
          <Skeleton className="mt-3 h-8 w-3/4" />
          <Skeleton className="mt-2 h-4 w-1/2" />
        </div>
        <div className={cn("flex flex-col gap-3", density === "compact" ? "p-4" : density === "spacious" ? "p-6" : "p-5")}>
          {Array.from({ length: 6 }).map((_, index) => (
            <div key={index} className="flex items-center justify-between gap-4">
              <Skeleton className="h-4 w-24" />
              <Skeleton className="h-4 w-32" />
            </div>
          ))}
        </div>
      </div>
    )
  }

  if (error) {
    return (
      <DetailPanelState
        appearance={appearance}
        radius={radius}
        className={className}
        icon={AlertCircle}
        title="Failed to load record"
        description={error.message}
        tone="error"
        action={(
          <Button variant="outline" size="sm" onClick={() => void refreshRecord()}>
            <RefreshCw className="mr-2 size-3.5" />
            Retry
          </Button>
        )}
      />
    )
  }

  if (!table || !record) {
    return (
      <DetailPanelState
        appearance={appearance}
        radius={radius}
        className={className}
        icon={FileText}
        title="Record not found"
        description="The requested record could not be found."
      />
    )
  }

  const actionNodes = actions?.length ? (
    <>
      {actions.map((action) => {
        const Icon = action.icon
        const isLoading = actionLoading === action.label
        return (
          <Button
            key={action.label}
            type="button"
            variant={action.variant ?? "outline"}
            size="sm"
            disabled={isLoading}
            className={cn("gap-1.5", recordsRadiusClassName(radius, "control"))}
            onClick={() => void handleAction(action)}
          >
            {isLoading ? (
              <Loader2 className="size-3.5 animate-spin" />
            ) : Icon ? (
              <Icon className="size-3.5" />
            ) : null}
            {action.label}
          </Button>
        )
      })}
    </>
  ) : undefined

  return (
    <RecordDetail
      record={record}
      table={table}
      client={client}
      podId={podId}
      mode={mode}
      variant={variant}
      tabs={resolvedTabs}
      headerFields={headerFields}
      fieldGroups={fieldGroups}
      relatedRecords={relatedRecords}
      titleField={titleField}
      descriptionField={descriptionField}
      identifierField={identifierField}
      statusField={statusField}
      updateVia={updateVia}
      updateFunctionName={updateFunctionName}
      foreignKeyLabels={foreignKeyLabels}
      enumColorMap={enumColorMap}
      appearance={appearance}
      density={density}
      radius={radius}
      layout={layout}
      actions={actionNodes}
      tableLabel={tableLabel}
      sectionLabels={sectionLabels}
      sectionVisibility={sectionVisibility}
      className={className}
      onRecordChanged={() => void refreshRecord()}
      onForeignKeyNavigate={(column, id) => {
        const reference = column.foreign_key?.references ?? ""
        const refTable = reference.split(".")[0] || column.name
        onNavigate?.(refTable, id)
      }}
      titleOverride={title}
    />
  )
}

function DetailPanelState({
  appearance,
  radius,
  className,
  icon: Icon,
  title,
  description,
  tone = "default",
  action,
}: {
  appearance: LemmaDetailPanelAppearance
  radius: LemmaDetailPanelRadius
  className?: string
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  tone?: "default" | "error"
  action?: React.ReactNode
}) {
  return (
    <div className={detailPanelSurfaceClassName(appearance, radius, className)}>
      <div className="flex h-full min-h-[18rem] flex-col items-center justify-center gap-3 px-6 py-8 text-center">
        <span
          className={cn(
            "flex size-11 items-center justify-center border border-border/60 bg-muted/40",
            recordsRadiusClassName(radius, "pill"),
            tone === "error" ? "text-destructive" : "text-muted-foreground",
          )}
        >
          <Icon className="size-5" />
        </span>
        <div className="flex flex-col gap-1">
          <p className="font-medium text-foreground">{title}</p>
          <p className="max-w-sm text-sm text-muted-foreground">{description}</p>
        </div>
        {action}
      </div>
    </div>
  )
}

function detailPanelSurfaceClassName(
  appearance: LemmaDetailPanelAppearance,
  radius: LemmaDetailPanelRadius,
  className?: string,
) {
  return cn(
    "lemma-detail-panel flex h-full min-h-0 flex-col overflow-hidden",
    recordsRadiusClassName(radius, "surface"),
    appearance === "minimal" ? "bg-transparent" : "bg-card",
    appearance === "borderless" || appearance === "minimal" ? "border-0 shadow-none" : "border border-border/50 shadow-sm",
    appearance === "contained" && "border-border/70 bg-card",
    className,
  )
}

function resolveLegacyDetailTabs(options: {
  detailTabs?: LegacyCompatibleDetailTab[]
  tabs?: DetailTab[]
  renderFiles?: (context: RecordDetailTabContext) => React.ReactNode
  renderComments?: (context: RecordDetailTabContext) => React.ReactNode
  renderActivity?: (context: RecordDetailTabContext) => React.ReactNode
}): Array<RecordDetailBuiltinTab | DetailTab> | undefined {
  const legacyTabs = buildLegacyDetailExtensionTabs(options)

  if (!options.detailTabs?.length && !options.tabs?.length) {
    if (legacyTabs.length === 0) return undefined
    return [
      "details",
      ...legacyTabs,
    ]
  }

  const combined = [
    ...(options.detailTabs ?? []),
    ...(options.tabs ?? []),
  ].flatMap<RecordDetailBuiltinTab | DetailTab>((tab) => {
    if (typeof tab !== "string") return [tab]
    if (tab === "details" || tab === "related") return [tab]
    const legacyTab = legacyTabs.find((candidate) => detailTabKey(candidate) === tab)
    return legacyTab ? [legacyTab] : []
  })

  const deduped = combined.filter(
    (tab, index, allTabs) =>
      allTabs.findIndex((candidate) => detailTabKey(candidate) === detailTabKey(tab)) === index,
  )

  return deduped.length > 0 ? deduped : undefined
}

function buildLegacyDetailExtensionTabs(options: {
  renderFiles?: (context: RecordDetailTabContext) => React.ReactNode
  renderComments?: (context: RecordDetailTabContext) => React.ReactNode
  renderActivity?: (context: RecordDetailTabContext) => React.ReactNode
}): DetailTab[] {
  const tabs: DetailTab[] = []

  if (options.renderComments) {
    tabs.push({ id: "comments", label: "Comments", render: options.renderComments })
  }

  if (options.renderActivity) {
    tabs.push({ id: "activity", label: "Activity", render: options.renderActivity })
  }

  if (options.renderFiles) {
    tabs.push({ id: "files", label: "Files", render: options.renderFiles })
  }

  return tabs
}

function detailTabKey(tab: RecordDetailBuiltinTab | DetailTab): string {
  return typeof tab === "string" ? tab : tab.id
}
