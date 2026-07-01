import * as React from "react"
import { Calendar, Database, ExternalLink, Link2, RefreshCw, Trash2 } from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/lemma/ui/tabs"
import { useForeignKeyOptions, useReferencingRecords, useUpdateRecord } from "lemma-sdk/react"
import {
  buildDefaultRecordDetailFieldGroups,
  detectRecordDescriptionColumn,
  detectRecordStatusColumn,
  detectRecordTitleColumn,
  formatRecordPlainValue,
  getRecordFieldKind,
  humanizeRecordFieldName,
  type ColumnSchema,
  type LemmaClient,
  type Table,
} from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import { RecordFieldInput, type SharedRecordFormFieldDescriptor } from "@/components/lemma/record-form-fields"
import { enumPillClasses, isSystemField, typeBadgeClasses, type EnumColorMap } from "./records-enum-utils"
import {
  displayColumnLabel,
  formatRecordFieldValue,
  pickPrimaryColumn,
  pickSecondaryColumns,
  shortenIdentifier,
  type ColumnLabelMap,
} from "./records-display-utils"
import {
  recordsRadiusClassName,
  type LemmaRecordsAppearance,
  type LemmaRecordsDensity,
  type LemmaRecordsRadius,
} from "./records-style-utils"

export type RecordDetailVariant = "summary" | "workspace" | "activity"
export type RecordDetailMode = "view" | "editable"
export type RecordDetailBuiltinTab = "details" | "related"
export interface RecordDetailTabContext {
  record: Record<string, unknown>
  table: Table
  recordId: string
}
export interface RecordDetailCustomTab {
  id: string
  label: React.ReactNode
  content?: React.ReactNode
  render?: (context: RecordDetailTabContext) => React.ReactNode
  visible?: boolean | ((context: RecordDetailTabContext) => boolean)
}
export type RecordDetailTab = RecordDetailBuiltinTab | RecordDetailCustomTab
export type RecordDetailLayout = "default" | "embedded"
export type RecordDetailSectionVisibilityRule = boolean | ((context: RecordDetailSectionVisibilityContext) => boolean)
export interface RecordDetailFieldGroup {
  label: string
  fields: string[]
}

export interface RecordDetailSectionVisibilityContext {
  record: Record<string, unknown>
  table: Table
  recordId: string
}

export interface RecordDetailRelatedRecord {
  tableName: string
  foreignKey: string
  label?: string
  fields?: string[]
  displayField?: string
  subtitleField?: string
  limit?: number
  sortBy?: string
  order?: "asc" | "desc" | string
  onRecordClick?: (record: Record<string, unknown>) => void
}

export interface RecordDetailProps {
  record: Record<string, unknown>
  table: Table
  client: LemmaClient
  podId?: string
  mode?: RecordDetailMode
  variant?: RecordDetailVariant
  tabs?: RecordDetailTab[]
  headerFields?: string[]
  fieldGroups?: RecordDetailFieldGroup[]
  relatedRecords?: RecordDetailRelatedRecord[]
  hiddenFields?: string[]
  titleField?: string
  descriptionField?: string
  identifierField?: string
  statusField?: string
  updateVia?: "direct" | "function"
  updateFunctionName?: string
  columnLabels?: ColumnLabelMap
  foreignKeyLabels?: Record<string, string>
  enumColorMap?: EnumColorMap
  appearance?: LemmaRecordsAppearance
  density?: LemmaRecordsDensity
  radius?: LemmaRecordsRadius
  layout?: RecordDetailLayout
  actions?: React.ReactNode
  sectionLabels?: Partial<Record<string, React.ReactNode>>
  sectionVisibility?: Partial<Record<string, RecordDetailSectionVisibilityRule>>
  className?: string
  onRecordChanged?: () => void
  onDelete?: () => void
  onForeignKeyNavigate?: (column: ColumnSchema, id: string) => void
  tableLabel?: React.ReactNode
  titleOverride?: React.ReactNode
}

export function RecordDetail({
  record,
  table,
  client,
  podId,
  mode = "editable",
  variant = "workspace",
  tabs,
  headerFields,
  fieldGroups,
  relatedRecords = [],
  hiddenFields = [],
  titleField,
  descriptionField,
  identifierField,
  statusField,
  updateVia,
  updateFunctionName,
  columnLabels,
  foreignKeyLabels,
  enumColorMap,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  layout = "default",
  actions,
  sectionLabels,
  sectionVisibility,
  className,
  onRecordChanged,
  onDelete,
  onForeignKeyNavigate,
  tableLabel,
  titleOverride,
}: RecordDetailProps) {
  const primaryKey = table.primary_key_column || "id"
  const recordId = String(record[primaryKey] ?? "")
  const columns = React.useMemo(
    () =>
      table.columns
        .filter((column) => !hiddenFields.includes(column.name))
        .filter((column) => column.type !== "VECTOR"),
    [hiddenFields, table.columns],
  )
  const userColumns = columns.filter((column) => !isSystemField(column))
  const primaryColumn = pickPrimaryColumn(userColumns)
  const secondaryColumns = pickSecondaryColumns(userColumns, primaryColumn, { count: 3 })
  const resolvedTitleField = React.useMemo(
    () => titleField ?? detectRecordTitleColumn(userColumns)?.name,
    [titleField, userColumns],
  )
  const resolvedDescriptionField = React.useMemo(
    () => descriptionField ?? detectRecordDescriptionColumn(userColumns)?.name,
    [descriptionField, userColumns],
  )
  const resolvedStatusField = React.useMemo(
    () => statusField ?? detectRecordStatusColumn(userColumns)?.name,
    [statusField, userColumns],
  )
  const resolvedFieldGroups = React.useMemo<RecordDetailFieldGroup[]>(() => {
    if (fieldGroups?.length) return fieldGroups
    return buildDefaultRecordDetailFieldGroups(userColumns, {
      titleField: resolvedTitleField,
      descriptionField: resolvedDescriptionField,
      statusField: resolvedStatusField,
      identifierField,
    })
  }, [fieldGroups, identifierField, resolvedDescriptionField, resolvedStatusField, resolvedTitleField, userColumns])
  const headerColumns = React.useMemo(() => {
    if (headerFields?.length) {
      return headerFields
        .map((fieldName) => userColumns.find((column) => column.name === fieldName))
        .filter((column): column is ColumnSchema => Boolean(column))
    }
    return secondaryColumns
  }, [headerFields, secondaryColumns, userColumns])
  const title = formatRecordPlainValue(
    resolvedTitleField ? record[resolvedTitleField] : primaryColumn ? record[primaryColumn.name] : undefined,
  ) || "Untitled record"
  const titleContent = titleOverride ?? title
  const description = formatRecordPlainValue(resolvedDescriptionField ? record[resolvedDescriptionField] : undefined)
  const statusColumn = resolvedStatusField
    ? userColumns.find((column) => column.name === resolvedStatusField)
    : undefined
  const statusValue = resolvedStatusField ? record[resolvedStatusField] : undefined
  const identifierValue = identifierField ? record[identifierField] : recordId
  const sectionContext = React.useMemo<RecordDetailSectionVisibilityContext>(
    () => ({ record, table, recordId }),
    [record, recordId, table],
  )
  const tabContext = React.useMemo<RecordDetailTabContext>(
    () => ({ record, table, recordId }),
    [record, recordId, table],
  )
  const activeTabs = React.useMemo(() => {
    return normalizeRecordDetailTabs(tabs, { sectionLabels }).filter((tab) => {
      if (!resolveDetailSectionVisibility(sectionVisibility?.[tab.id], sectionContext)) {
        return false
      }
      if (typeof tab.visible === "boolean") return tab.visible
      if (typeof tab.visible === "function") return tab.visible(tabContext)
      return true
    })
  }, [sectionContext, sectionLabels, sectionVisibility, tabContext, tabs])
  const defaultTab = activeTabs[0]?.id ?? "details"
  const singleActiveTab = activeTabs.length === 1 ? activeTabs[0] : null
  const isEmbedded = layout === "embedded"
  const hasTableLabel = tableLabel != null && tableLabel !== false

  const renderTabContent = (tab: {
    id: string
    builtin: boolean
    content?: React.ReactNode
    render?: (context: RecordDetailTabContext) => React.ReactNode
  }) => {
    if (tab.id === "details") {
      return (
        <DetailsTab
          record={record}
          columns={userColumns}
          client={client}
          podId={podId}
          tableName={table.name}
          recordId={recordId}
          mode={mode}
          variant={variant}
          fieldGroups={resolvedFieldGroups}
          columnLabels={columnLabels}
          updateVia={updateVia}
          updateFunctionName={updateFunctionName}
          foreignKeyLabels={foreignKeyLabels}
          enumColorMap={enumColorMap}
          density={density}
          radius={radius}
          layout={layout}
          onRecordChanged={onRecordChanged}
          onForeignKeyNavigate={onForeignKeyNavigate}
        />
      )
    }

    if (tab.id === "related") {
      return (
        <RelatedTab
          recordId={recordId}
          configs={relatedRecords}
          client={client}
          podId={podId}
          appearance={appearance}
          density={density}
          radius={radius}
        />
      )
    }

    return tab.content ?? tab.render?.(tabContext) ?? null
  }

  return (
    <section
      data-appearance={appearance}
      data-density={density}
      data-radius={radius}
      className={cn(
        "lemma-record-detail flex min-h-0 min-w-0 flex-col overflow-hidden",
        detailSurfaceClassName(appearance, radius),
        className,
      )}
    >
      <div className={cn("flex min-h-0 flex-1 flex-col overflow-y-auto", isEmbedded ? "gap-4 p-4" : density === "compact" ? "gap-3 p-4" : density === "spacious" ? "gap-5 p-7" : "gap-4 p-6")}>
        {isEmbedded ? (
          <div
            className={cn(
              "sticky top-0 z-10 -mx-4 -mt-4 border-b border-border/30 px-4 py-4 backdrop-blur",
              appearance === "minimal" ? "bg-background/95" : "bg-card/95",
            )}
          >
            <div className="flex flex-col gap-4">
              <div className="flex flex-wrap items-start justify-between gap-3">
                <div className="flex min-w-0 items-start gap-3">
                  <div className={cn("flex shrink-0 items-center justify-center border border-border/50 bg-muted/35 text-muted-foreground", recordsRadiusClassName(radius, "control"), density === "compact" ? "size-9" : "size-10")}>
                    <Database className="size-4" />
                  </div>
                  <div className="min-w-0">
                    <div className="flex flex-wrap items-center gap-2">
                      {statusValue != null && statusValue !== "" ? (
                        statusColumn?.type === "ENUM" && statusColumn.options?.length ? (
                          <span className={enumPillClasses(String(statusValue), statusColumn.options, enumColorMap)}>
                            {String(statusValue)}
                          </span>
                        ) : (
                          <span className={cn("inline-flex items-center border border-border/50 bg-muted/40 px-2 py-0.5 text-[11px] font-medium text-foreground", recordsRadiusClassName(radius, "pill"))}>
                            {String(statusValue)}
                          </span>
                        )
                      ) : null}
                      {identifierValue != null && identifierValue !== "" ? (
                        <span className={cn("inline-flex items-center border border-border/50 bg-muted/40 px-2 py-0.5 font-mono text-[11px] text-muted-foreground", recordsRadiusClassName(radius, "pill"))}>
                          {shortenIdentifier(identifierValue)}
                        </span>
                      ) : null}
                    </div>
                    {hasTableLabel ? (
                      <p className="mt-3 text-[11px] font-medium uppercase tracking-[0.18em] text-muted-foreground/80">
                        {tableLabel}
                      </p>
                    ) : null}
                  </div>
                </div>
                <div className="min-w-0">
                  <h2
                    className={cn(
                      "break-words tracking-tight text-foreground",
                      density === "compact"
                        ? "text-[1.45rem] font-semibold leading-[1.08]"
                        : "text-[1.7rem] font-semibold leading-[1.04]",
                    )}
                  >
                    {titleContent}
                  </h2>
                  {description ? (
                    <p className="mt-3 max-w-3xl break-words text-sm leading-6 text-muted-foreground">{description}</p>
                  ) : null}
                  {headerColumns.length > 0 ? (
                    <div className="mt-4 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                      {headerColumns.map((column) => {
                        const value = record[column.name]
                        if (value == null || value === "") return null
                        return (
                          <span
                            key={column.name}
                            className={cn(
                              "inline-flex max-w-full items-center gap-1.5 border border-border/35 bg-muted/25 px-2.5 py-1",
                              recordsRadiusClassName(radius, "pill"),
                            )}
                          >
                            <span className="text-muted-foreground/75">{displayColumnLabel(column.name, columnLabels)}</span>
                            <span className="break-words text-foreground">
                              {formatRecordFieldValue(value, column, undefined, enumColorMap)}
                            </span>
                          </span>
                        )
                      })}
                    </div>
                  ) : null}
                </div>
              </div>
              {actions || onDelete ? (
                <div className="flex min-w-0 flex-col gap-2 border-t border-border/30 pt-3 sm:flex-row sm:items-center sm:justify-between">
                  {actions ? (
                    <div className="min-w-0 flex-1 overflow-x-auto pb-1">
                      {actions}
                    </div>
                  ) : null}
                  {onDelete ? (
                    <Button
                      type="button"
                      variant="ghost"
                      size="sm"
                      className="shrink-0 text-destructive hover:bg-destructive/10 hover:text-destructive"
                      onClick={onDelete}
                    >
                      <Trash2 data-icon="inline-start" />
                      Delete
                    </Button>
                  ) : null}
                </div>
              ) : null}
            </div>
          </div>
        ) : (
          <div className="flex flex-col gap-4 xl:flex-row xl:items-start xl:justify-between">
            <div className="flex min-w-0 items-start gap-3">
              <div className={cn("flex shrink-0 items-center justify-center border border-border/50 bg-muted/35 text-muted-foreground", recordsRadiusClassName(radius, "control"), density === "compact" ? "size-9" : "size-10")}>
                <Database className="size-4" />
              </div>
              <div className="min-w-0 flex-1">
                <div className="flex flex-wrap items-center gap-2">
                  {statusValue != null && statusValue !== "" ? (
                    statusColumn?.type === "ENUM" && statusColumn.options?.length ? (
                      <span className={enumPillClasses(String(statusValue), statusColumn.options, enumColorMap)}>
                        {String(statusValue)}
                      </span>
                    ) : (
                      <span className={cn("inline-flex items-center border border-border/50 bg-muted/40 px-2 py-0.5 text-[11px] font-medium text-foreground", recordsRadiusClassName(radius, "pill"))}>
                        {String(statusValue)}
                      </span>
                    )
                  ) : null}
                  {identifierValue != null && identifierValue !== "" ? (
                    <span className={cn("inline-flex items-center border border-border/50 bg-muted/40 px-2 py-0.5 font-mono text-[11px] text-muted-foreground", recordsRadiusClassName(radius, "pill"))}>
                      {shortenIdentifier(identifierValue)}
                    </span>
                  ) : null}
                </div>
                <h2 className={cn("mt-2 break-words tracking-tight text-foreground", density === "compact" ? "text-xl font-semibold leading-tight" : "text-[2rem] font-semibold leading-tight")}>
                  {titleContent}
                </h2>
                {description ? (
                  <p className="mt-2 max-w-3xl text-sm text-muted-foreground">{description}</p>
                ) : null}
                {(hasTableLabel || headerColumns.length > 0) ? (
                  <div className="mt-2 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                    {hasTableLabel ? <span>{tableLabel}</span> : null}
                    {headerColumns.map((column) => {
                      const value = record[column.name]
                      if (value == null || value === "") return null
                      return (
                        <span key={column.name} className={cn("inline-flex max-w-64 items-center gap-1 bg-muted/35 px-2 py-0.5 truncate", recordsRadiusClassName(radius, "pill"))}>
                          <span className="text-muted-foreground/75">{displayColumnLabel(column.name, columnLabels)}</span>
                          <span className="truncate text-foreground">{formatRecordFieldValue(value, column, undefined, enumColorMap)}</span>
                        </span>
                      )
                    })}
                  </div>
                ) : null}
              </div>
            </div>
            <div className="flex shrink-0 items-center gap-2">
              {actions}
              {onDelete ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="text-destructive hover:bg-destructive/10 hover:text-destructive"
                  onClick={onDelete}
                >
                  <Trash2 data-icon="inline-start" />
                  Delete
                </Button>
              ) : null}
            </div>
          </div>
        )}

        {singleActiveTab ? (
          <div className="mt-4 min-h-0 min-w-0 flex-1">
            {renderTabContent(singleActiveTab)}
          </div>
        ) : activeTabs.length > 1 ? (
          <Tabs defaultValue={defaultTab} className="min-h-0 w-full flex-1 flex-col">
            <TabsList className={cn("w-full shrink-0 justify-start overflow-x-auto", recordsRadiusClassName(radius, "control"))}>
              {activeTabs.map((tab) => (
                <TabsTrigger key={tab.id} value={tab.id}>
                  {tab.label}
                </TabsTrigger>
              ))}
            </TabsList>

            {activeTabs.map((tab) => (
              <TabsContent key={tab.id} value={tab.id} className="mt-4 min-w-0">
                {renderTabContent(tab)}
              </TabsContent>
            ))}
          </Tabs>
        ) : null}
      </div>
    </section>
  )
}

function normalizeRecordDetailTabs(
  tabs: RecordDetailTab[] | undefined,
  options: {
    sectionLabels?: Partial<Record<string, React.ReactNode>>
  },
): Array<{
  id: string
  label: React.ReactNode
  builtin: boolean
  content?: React.ReactNode
  render?: (context: RecordDetailTabContext) => React.ReactNode
  visible?: boolean | ((context: RecordDetailTabContext) => boolean)
}> {
  const requestedTabs = tabs?.length
    ? tabs
    : defaultRecordDetailTabs()

  const normalizedTabs = requestedTabs.map((tab) => {
    if (typeof tab === "string") {
      return {
        id: tab,
        label: options.sectionLabels?.[tab] ?? recordDetailTabLabel(tab),
        builtin: true,
      }
    }

    return {
      id: tab.id,
      label: tab.label,
      builtin: false,
      content: tab.content,
      render: tab.render,
      visible: tab.visible,
    }
  })

  return normalizedTabs.filter(
    (tab, index, allTabs) => allTabs.findIndex((candidate) => candidate.id === tab.id) === index,
  )
}

function defaultRecordDetailTabs(): RecordDetailBuiltinTab[] {
  return ["details"]
}

function recordDetailTabLabel(tab: RecordDetailBuiltinTab): string {
  if (tab === "details") return "Details"
  return "Related"
}

function resolveDetailSectionVisibility(
  rule: RecordDetailSectionVisibilityRule | undefined,
  context: RecordDetailSectionVisibilityContext,
): boolean {
  if (typeof rule === "boolean") return rule
  if (typeof rule === "function") return rule(context)
  return true
}

function DetailsTab({
  record,
  columns,
  client,
  podId,
  tableName,
  recordId,
  mode,
  variant,
  fieldGroups,
  columnLabels,
  updateVia,
  updateFunctionName,
  foreignKeyLabels,
  enumColorMap,
  density,
  radius,
  layout,
  onRecordChanged,
  onForeignKeyNavigate,
}: {
  record: Record<string, unknown>
  columns: ColumnSchema[]
  client: LemmaClient
  podId?: string
  tableName: string
  recordId: string
  mode: RecordDetailMode
  variant: RecordDetailVariant
  fieldGroups: RecordDetailFieldGroup[]
  columnLabels?: ColumnLabelMap
  updateVia?: "direct" | "function"
  updateFunctionName?: string
  foreignKeyLabels?: Record<string, string>
  enumColorMap?: EnumColorMap
  density: LemmaRecordsDensity
  radius: LemmaRecordsRadius
  layout: RecordDetailLayout
  onRecordChanged?: () => void
  onForeignKeyNavigate?: (column: ColumnSchema, id: string) => void
}) {
  const gridClassName = variant === "summary" || mode === "view"
    ? "grid-cols-1"
    : "grid-cols-1 md:grid-cols-2"

  return (
    <div className={cn("flex flex-col", density === "compact" ? "gap-3" : density === "spacious" ? "gap-5" : "gap-4")}>
      {fieldGroups.map((group) => {
        const groupColumns = group.fields
          .map((fieldName) => columns.find((column) => column.name === fieldName))
          .filter((column): column is ColumnSchema => Boolean(column))

        if (groupColumns.length === 0) return null

        return (
          <section key={group.label}>
            <div className="mb-3 flex items-center gap-3">
              <p className="shrink-0 text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">{group.label}</p>
              <span className="h-px flex-1 bg-border/30" />
            </div>
            {mode === "view" ? (
              <div className="divide-y divide-border/30">
                {groupColumns.map((column) => (
                  <RecordField
                    key={column.name}
                    record={record}
                    column={column}
                    label={displayColumnLabel(column.name, columnLabels)}
                    client={client}
                    podId={podId}
                    tableName={tableName}
                    recordId={recordId}
                    mode={mode}
                    updateVia={updateVia}
                    updateFunctionName={updateFunctionName}
                    foreignKeyLabels={foreignKeyLabels}
                    enumColorMap={enumColorMap}
                    density={density}
                    radius={radius}
                    layout={layout}
                    onRecordChanged={onRecordChanged}
                    onForeignKeyNavigate={onForeignKeyNavigate}
                  />
                ))}
              </div>
            ) : (
              <div className={cn("grid", gridClassName, density === "compact" ? "gap-2" : density === "spacious" ? "gap-4" : "gap-3")}>
                {groupColumns.map((column) => (
                  <RecordField
                    key={column.name}
                    record={record}
                    column={column}
                    label={displayColumnLabel(column.name, columnLabels)}
                    client={client}
                    podId={podId}
                    tableName={tableName}
                    recordId={recordId}
                    mode={mode}
                    updateVia={updateVia}
                    updateFunctionName={updateFunctionName}
                    foreignKeyLabels={foreignKeyLabels}
                    enumColorMap={enumColorMap}
                    density={density}
                    radius={radius}
                    layout={layout}
                    onRecordChanged={onRecordChanged}
                    onForeignKeyNavigate={onForeignKeyNavigate}
                  />
                ))}
              </div>
            )}
          </section>
        )
      })}
    </div>
  )
}

function RecordField({
  record,
  column,
  label,
  client,
  podId,
  tableName,
  recordId,
  mode,
  updateVia,
  updateFunctionName,
  foreignKeyLabels,
  enumColorMap,
  density,
  radius,
  layout,
  onRecordChanged,
  onForeignKeyNavigate,
}: {
  record: Record<string, unknown>
  column: ColumnSchema
  label: string
  client: LemmaClient
  podId?: string
  tableName: string
  recordId: string
  mode: RecordDetailMode
  updateVia?: "direct" | "function"
  updateFunctionName?: string
  foreignKeyLabels?: Record<string, string>
  enumColorMap?: EnumColorMap
  density: LemmaRecordsDensity
  radius: LemmaRecordsRadius
  layout: RecordDetailLayout
  onRecordChanged?: () => void
  onForeignKeyNavigate?: (column: ColumnSchema, id: string) => void
}) {
  const value = record[column.name]
  const updateMutation = useUpdateRecord({ client, podId, tableName, recordId, updateVia, updateFunctionName })
  const save = async (nextValue: unknown) => {
    await updateMutation.update({ [column.name]: nextValue })
    onRecordChanged?.()
  }

  if (mode === "view") {
    return (
      layout === "embedded" ? (
        <div className="grid gap-2 py-3 sm:grid-cols-[minmax(8rem,10rem)_minmax(0,1fr)] sm:items-start sm:gap-4">
          <p className="text-[11px] font-medium leading-5 text-muted-foreground">
            {label}
          </p>
          <div className="min-w-0">
            <ReadOnlyFieldValue
              value={value}
              column={column}
              client={client}
              podId={podId}
              tableName={tableName}
              labelField={foreignKeyLabels?.[column.name]}
              enumColorMap={enumColorMap}
              radius={radius}
              layout={layout}
              onForeignKeyNavigate={onForeignKeyNavigate}
            />
          </div>
        </div>
      ) : (
        <div className={cn("grid gap-2 py-3", density === "compact" ? "sm:grid-cols-[minmax(6.5rem,8rem)_minmax(0,1fr)]" : "sm:grid-cols-[minmax(7.5rem,9rem)_minmax(0,1fr)] sm:gap-4")}>
          <div className="flex items-center gap-2 sm:pt-1">
            <p className="truncate text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
              {label}
            </p>
            <span className={typeBadgeClasses(column)}>
              {column.foreign_key ? "ref" : column.type.toLowerCase()}
            </span>
          </div>
          <div className="min-w-0">
            <ReadOnlyFieldValue
              value={value}
              column={column}
              client={client}
              podId={podId}
              tableName={tableName}
              labelField={foreignKeyLabels?.[column.name]}
              enumColorMap={enumColorMap}
              radius={radius}
              layout={layout}
              onForeignKeyNavigate={onForeignKeyNavigate}
            />
          </div>
        </div>
      )
    )
  }

  return (
    <div className={cn("group border border-border/40 bg-muted/15", recordsRadiusClassName(radius, "surface"), density === "compact" ? "p-3" : density === "spacious" ? "p-4" : "p-3.5")}>
      <div className="mb-2 flex items-center gap-2">
        <p className="truncate text-[10px] font-semibold uppercase tracking-widest text-muted-foreground">
          {label}
        </p>
        <span className={typeBadgeClasses(column)}>
          {column.foreign_key ? "ref" : column.type.toLowerCase()}
        </span>
      </div>
      {mode === "editable" ? (
        <EditableFieldValue
          value={value}
          column={column}
          client={client}
          podId={podId}
          tableName={tableName}
          labelField={foreignKeyLabels?.[column.name]}
          radius={radius}
          disabled={updateMutation.isSubmitting}
          onSave={save}
        />
      ) : (
        <ReadOnlyFieldValue
          value={value}
          column={column}
          client={client}
          podId={podId}
          tableName={tableName}
          labelField={foreignKeyLabels?.[column.name]}
          enumColorMap={enumColorMap}
          radius={radius}
          layout={layout}
          onForeignKeyNavigate={onForeignKeyNavigate}
        />
      )}
      {updateMutation.error ? (
        <p className="mt-2 text-xs text-destructive">{updateMutation.error.message}</p>
      ) : null}
    </div>
  )
}

function ReadOnlyFieldValue({
  value,
  column,
  client,
  podId,
  tableName,
  labelField,
  enumColorMap,
  radius,
  layout = "default",
  onForeignKeyNavigate,
}: {
  value: unknown
  column: ColumnSchema
  client: LemmaClient
  podId?: string
  tableName: string
  labelField?: string
  enumColorMap?: EnumColorMap
  radius: LemmaRecordsRadius
  layout?: RecordDetailLayout
  onForeignKeyNavigate?: (column: ColumnSchema, id: string) => void
}) {
  const fkOptions = useForeignKeyOptions({
    client,
    podId,
    tableName,
    columnName: column.name,
    labelField,
    enabled: !!column.foreign_key,
  })
  const foreignKeyReference = resolveForeignKeyReference(value)
  const resolvedLabel = foreignKeyReference.label
    ?? fkOptions.options.find((option) => String(option.value) === foreignKeyReference.id)?.label

  if (value == null || value === "") return <p className="text-sm italic text-muted-foreground">Empty</p>
  if (column.foreign_key) {
    const foreignKeyId = foreignKeyReference.id
    const foreignKeyLabel = resolvedLabel ?? shortenIdentifier(foreignKeyId ?? value)

    if (foreignKeyId && onForeignKeyNavigate) {
      return (
        <button
          type="button"
          className={cn(
            "inline-flex max-w-full items-center gap-1.5 text-left text-sm font-medium text-primary hover:underline",
            layout === "embedded" ? "break-words leading-6" : "truncate",
          )}
          onClick={() => onForeignKeyNavigate(column, foreignKeyId)}
        >
          <span className={cn("min-w-0", layout === "embedded" ? "break-words" : "truncate")}>
            {foreignKeyLabel}
          </span>
          <ExternalLink className="size-3.5 shrink-0" />
        </button>
      )
    }

    return (
      <p className={cn("text-sm font-medium text-foreground", layout === "embedded" ? "break-words leading-6" : "truncate")}>
        {foreignKeyLabel}
      </p>
    )
  }
  if (column.type === "JSON") {
    const text = typeof value === "string" ? value : JSON.stringify(value, null, 2)
    return (
      <pre className={cn("max-h-48 overflow-auto border border-border/40 bg-background/60 p-3 font-mono text-xs text-foreground", recordsRadiusClassName(radius, "control"))}>
        {text}
      </pre>
    )
  }
  if (column.type === "DATE" || column.type === "DATETIME") {
    return (
      <p className={cn("flex items-center gap-1.5 text-sm text-foreground", layout === "embedded" && "leading-6")}>
        <Calendar className="size-3.5 text-muted-foreground" />
        {formatTimestamp(value)}
      </p>
    )
  }
  return (
    <div className={cn("break-words text-sm text-foreground", layout === "embedded" && "leading-6")}>
      {formatRecordFieldValue(value, column, undefined, enumColorMap)}
    </div>
  )
}

function EditableFieldValue({
  value,
  column,
  client,
  podId,
  tableName,
  labelField,
  radius,
  disabled,
  onSave,
}: {
  value: unknown
  column: ColumnSchema
  client: LemmaClient
  podId?: string
  tableName: string
  labelField?: string
  radius: LemmaRecordsRadius
  disabled?: boolean
  onSave: (value: unknown) => Promise<void>
}) {
  const [draft, setDraft] = React.useState<unknown>(value)
  const fieldKind = getRecordFieldKind(column)
  const field = React.useMemo<SharedRecordFormFieldDescriptor>(
    () => ({
      name: column.name,
      label: humanizeRecordFieldName(column.name),
      kind: fieldKind,
      column,
      options: column.options ?? undefined,
    }),
    [column, fieldKind],
  )

  React.useEffect(() => {
    setDraft(value)
  }, [value])

  const commitOnChange = fieldKind === "foreign-key" || fieldKind === "select" || fieldKind === "boolean"
  const commitOnBlur = !commitOnChange

  const commitDraft = React.useCallback(
    async (nextValue: unknown) => {
      await onSave(coerceEditableFieldValue(nextValue, column, fieldKind))
    },
    [column, fieldKind, onSave],
  )

  return (
    <RecordFieldInput
      field={field}
      value={draft}
      onChange={(nextValue) => {
        setDraft(nextValue)
        if (commitOnChange) {
          void commitDraft(nextValue)
        }
      }}
      client={client}
      podId={podId}
      tableName={tableName}
      labelField={labelField}
      radius={radius}
      controlSize="sm"
      controlClassName="border-border/70 bg-background/70"
      disabled={disabled}
      onBlur={commitOnBlur ? () => void commitDraft(draft) : undefined}
      onKeyDown={
        commitOnBlur && fieldKind !== "textarea" && fieldKind !== "json"
          ? (event) => {
              if (event.key === "Enter") {
                event.currentTarget.blur()
              }
            }
          : undefined
      }
    />
  )
}

function RelatedTab({
  recordId,
  configs,
  client,
  podId,
  appearance,
  density,
  radius,
}: {
  recordId: string
  configs: RecordDetailRelatedRecord[]
  client: LemmaClient
  podId?: string
  appearance: LemmaRecordsAppearance
  density: LemmaRecordsDensity
  radius: LemmaRecordsRadius
}) {
  if (configs.length === 0) {
    return (
      <EmptyDetailState
        icon={Link2}
        title="No related sections configured"
        description="Pass relatedRecords to show child tables like tasks, comments, emails, notes, line items, or activity history for this record."
        appearance={appearance}
        radius={radius}
      />
    )
  }

  return (
    <div className={cn("grid grid-cols-1", density === "compact" ? "gap-2" : density === "spacious" ? "gap-4" : "gap-3")}>
      {configs.map((config) => (
        <RelatedRecordsPanel
          key={`${config.tableName}:${config.foreignKey}`}
          recordId={recordId}
          config={config}
          client={client}
          podId={podId}
          appearance={appearance}
          radius={radius}
        />
      ))}
    </div>
  )
}

function RelatedRecordsPanel({
  recordId,
  config,
  client,
  podId,
  appearance,
  radius,
}: {
  recordId: string
  config: RecordDetailRelatedRecord
  client: LemmaClient
  podId?: string
  appearance: LemmaRecordsAppearance
  radius: LemmaRecordsRadius
}) {
  const related = useReferencingRecords({
    client,
    podId,
    table: config.tableName,
    foreignKey: config.foreignKey,
    recordId,
    fields: config.fields,
    limit: config.limit ?? 8,
    sortBy: config.sortBy,
    order: config.order,
  })
  const table = related.referencedTable
  const primaryColumn = table
    ? table.columns.find((column) => column.name === config.displayField) ?? pickPrimaryColumn(table.columns)
    : undefined
  const subtitleColumn = table && config.subtitleField
    ? table.columns.find((column) => column.name === config.subtitleField)
    : undefined

  return (
    <div className={cn("border border-border/40", relatedPanelClassName(appearance), recordsRadiusClassName(radius, "surface"))}>
      <div className="flex items-center justify-between gap-3 border-b border-border/25 px-3 py-2.5">
        <div className="min-w-0">
          <p className="truncate text-sm font-medium text-foreground">{config.label ?? humanizeRecordFieldName(config.tableName)}</p>
          <p className="text-xs text-muted-foreground">{config.foreignKey} links back to this record</p>
        </div>
        <span className={cn("shrink-0 bg-muted/50 px-2 py-0.5 text-xs text-muted-foreground", recordsRadiusClassName(radius, "pill"))}>
          {related.isLoading ? "..." : related.total}
        </span>
      </div>
      <div className="p-2">
        {related.isLoading ? (
          <div className="flex items-center gap-2 px-2 py-3 text-sm text-muted-foreground">
            <RefreshCw className="size-4 animate-spin" />
            Loading related records...
          </div>
        ) : related.error ? (
          <p className="px-2 py-3 text-sm text-destructive">{related.error.message}</p>
        ) : related.records.length === 0 ? (
          <p className="px-2 py-3 text-sm text-muted-foreground">No related records yet.</p>
        ) : (
          <div className="flex flex-col gap-1">
            {related.records.map((childRecord, index) => {
              const title = formatRecordPlainValue(primaryColumn ? childRecord[primaryColumn.name] : undefined) || "Untitled"
              const subtitle = subtitleColumn ? formatRecordPlainValue(childRecord[subtitleColumn.name]) : null
              return (
                <button
                  key={String(childRecord.id ?? index)}
                  type="button"
                  className={cn(
                    "flex w-full items-start justify-between gap-3 px-3 py-2 text-left transition-colors hover:bg-muted/45",
                    recordsRadiusClassName(radius, "control"),
                  )}
                  onClick={() => config.onRecordClick?.(childRecord)}
                >
                  <span className="min-w-0">
                    <span className="block truncate text-sm font-medium text-foreground">{title}</span>
                    {subtitle ? <span className="mt-0.5 block truncate text-xs text-muted-foreground">{subtitle}</span> : null}
                  </span>
                  <span className="shrink-0 text-xs text-muted-foreground">{shortenIdentifier(childRecord.id ?? index)}</span>
                </button>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}

function EmptyDetailState({
  title,
  description,
  icon: Icon,
  appearance,
  radius,
}: {
  title: string
  description: string
  icon: React.ComponentType<{ className?: string }>
  appearance: LemmaRecordsAppearance
  radius: LemmaRecordsRadius
}) {
  return (
    <div className={cn("flex min-h-48 flex-col items-center justify-center gap-3 border border-dashed border-border/40 px-6 py-8 text-center", appearance === "minimal" ? "bg-transparent" : "bg-muted/15", recordsRadiusClassName(radius, "surface"))}>
      <div className={cn("flex size-10 items-center justify-center border border-border/50 bg-muted/35 text-muted-foreground", recordsRadiusClassName(radius, "pill"))}>
        <Icon className="size-4" />
      </div>
      <div>
        <p className="font-medium text-foreground">{title}</p>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}

function detailSurfaceClassName(appearance: LemmaRecordsAppearance, radius: LemmaRecordsRadius) {
  return cn(
    recordsRadiusClassName(radius, "surface"),
    appearance === "minimal" ? "bg-transparent" : "bg-card",
    appearance === "borderless" || appearance === "minimal" ? "border-0 shadow-none" : "border border-border/50 shadow-sm",
    appearance === "contained" && "border-border/70 bg-card",
  )
}

function relatedPanelClassName(appearance: LemmaRecordsAppearance) {
  if (appearance === "minimal" || appearance === "borderless") return "bg-transparent"
  return "bg-card/80"
}

function formatTimestamp(value: unknown): string {
  if (value == null || value === "") return "—"
  const date = value instanceof Date ? value : new Date(String(value))
  if (Number.isNaN(date.getTime())) return String(value)
  return date.toLocaleDateString(undefined, {
    year: "numeric",
    month: "short",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  })
}

function resolveForeignKeyReference(value: unknown): { id: string | null; label?: string } {
  if (value == null || value === "") {
    return { id: null }
  }

  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const objectValue = value as Record<string, unknown>
    const label = objectValue.name ?? objectValue.title ?? objectValue.label
    return {
      id: objectValue.id != null ? String(objectValue.id) : null,
      label: label != null ? String(label) : undefined,
    }
  }

  return { id: String(value) }
}

function coerceEditableFieldValue(value: unknown, column: ColumnSchema, fieldKind: string): unknown {
  if (fieldKind === "boolean") {
    return value === true
  }

  if (fieldKind === "json") {
    if (typeof value !== "string" || !value.trim()) return null
    try {
      return JSON.parse(value)
    } catch {
      return value
    }
  }

  const stringValue = value == null ? "" : String(value)
  if (!stringValue) return null
  if (column.type === "INTEGER") return Number.parseInt(stringValue, 10)
  if (column.type === "FLOAT") return Number.parseFloat(stringValue)
  return stringValue
}
