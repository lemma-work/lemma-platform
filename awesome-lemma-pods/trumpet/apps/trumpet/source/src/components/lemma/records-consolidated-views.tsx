import * as React from "react"
import { Calendar, Grid2X2, Rows3 } from "lucide-react"
import { Checkbox } from "@/components/lemma/ui/checkbox"
import type { ColumnSchema, Table } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import { isSystemField, type EnumColorMap } from "./records-enum-utils"
import {
  displayColumnLabel,
  formatRecordFieldValue,
  formatRecordPlainText,
  pickColumn,
  pickPrimaryColumn,
  pickSecondaryColumns,
  type ColumnLabelMap,
  type ForeignKeyLabelMap,
  type RecordPreviewDisplayOptions,
} from "./records-display-utils"
import { RecordQuickActionButtons, type RecordQuickAction } from "./records-quick-actions"
import {
  recordsRadiusClassName,
  type LemmaRecordsAppearance,
  type LemmaRecordsDensity,
  type LemmaRecordsRadius,
} from "./records-style-utils"

interface ConsolidatedViewBaseProps {
  records: Record<string, unknown>[]
  table: Table
  visibleColumns: ColumnSchema[]
  primaryKey: string
  selectedRecords: Set<string>
  selectionEnabled?: boolean
  onSelectRecord?: (id: string) => void
  onRecordClick: (record: Record<string, unknown>) => void
  foreignKeyLabelMap?: ForeignKeyLabelMap
  columnLabels?: ColumnLabelMap
  displayOptions?: RecordPreviewDisplayOptions
  quickActions?: RecordQuickAction[]
  onQuickAction?: (action: RecordQuickAction, record: Record<string, unknown>, index: number) => void
  pendingActionKey?: string | null
  enumColorMap?: EnumColorMap
  appearance: LemmaRecordsAppearance
  density: LemmaRecordsDensity
  radius: LemmaRecordsRadius
}

export interface RecordsCalendarViewProps extends ConsolidatedViewBaseProps {
  dateField?: string
}

export interface RecordsTimelineViewProps extends ConsolidatedViewBaseProps {
  dateField?: string
}

export interface RecordsMatrixViewProps extends ConsolidatedViewBaseProps {
  rowField?: string
  columnField?: string
}

export function RecordsCalendarView({
  records,
  table,
  visibleColumns,
  primaryKey,
  selectedRecords,
  selectionEnabled = true,
  onSelectRecord,
  onRecordClick,
  foreignKeyLabelMap,
  columnLabels,
  displayOptions,
  quickActions,
  onQuickAction,
  pendingActionKey,
  enumColorMap,
  appearance,
  density,
  radius,
  dateField,
}: RecordsCalendarViewProps) {
  const [cursor, setCursor] = React.useState(() => startOfMonth(new Date()))
  const dateColumn = pickDateColumn(visibleColumns, table.columns, dateField)
  const monthCells = React.useMemo(() => buildMonthCells(cursor), [cursor])
  const recordsByDay = React.useMemo(() => {
    const groups = new Map<string, Record<string, unknown>[]>()
    if (!dateColumn) return groups

    for (const record of records) {
      const date = coerceDate(record[dateColumn.name])
      if (!date) continue
      const key = dayKey(date)
      const group = groups.get(key) ?? []
      group.push(record)
      groups.set(key, group)
    }

    return groups
  }, [dateColumn, records])

  if (!dateColumn) {
    return (
      <MissingViewConfig
        icon={Calendar}
        title="Pick a date field for calendar"
        description="Pass calendarField or include a DATE/DATETIME column such as due_date, starts_at, created_at, or updated_at."
        appearance={appearance}
        radius={radius}
      />
    )
  }

  return (
    <div className="flex min-w-[48rem] flex-col">
      <div className={cn("flex items-center justify-between border-b border-border/30", density === "compact" ? "px-3 py-2" : "px-4 py-3")}>
        <div>
          <p className="text-sm font-medium text-foreground">
            {cursor.toLocaleDateString(undefined, { month: "long", year: "numeric" })}
          </p>
          <p className="text-xs text-muted-foreground">
            Scheduled by {displayColumnLabel(dateColumn.name, columnLabels)}
          </p>
        </div>
        <div className="flex items-center gap-1">
          <button
            type="button"
            className={cn("border border-border/50 bg-background px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground", recordsRadiusClassName(radius, "control"))}
            onClick={() => setCursor(addMonths(cursor, -1))}
          >
            Previous
          </button>
          <button
            type="button"
            className={cn("border border-border/50 bg-background px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground", recordsRadiusClassName(radius, "control"))}
            onClick={() => setCursor(startOfMonth(new Date()))}
          >
            Today
          </button>
          <button
            type="button"
            className={cn("border border-border/50 bg-background px-2 py-1 text-xs text-muted-foreground hover:bg-muted hover:text-foreground", recordsRadiusClassName(radius, "control"))}
            onClick={() => setCursor(addMonths(cursor, 1))}
          >
            Next
          </button>
        </div>
      </div>
      <div className="grid grid-cols-7 border-b border-border/30 bg-muted/20 text-[11px] font-medium uppercase tracking-widest text-muted-foreground">
        {["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"].map((day) => (
          <div key={day} className="px-3 py-2">{day}</div>
        ))}
      </div>
      <div className="grid grid-cols-7">
        {monthCells.map((day) => {
          const key = dayKey(day)
          const dayRecords = recordsByDay.get(key) ?? []
          const isMuted = day.getMonth() !== cursor.getMonth()
          const isToday = key === dayKey(new Date())

          return (
            <div
              key={key}
              className={cn(
                "min-h-36 border-b border-r border-border/25 p-2",
                isMuted ? "bg-muted/10 text-muted-foreground" : "bg-background/40",
              )}
            >
              <div className="mb-2 flex items-center justify-between">
                <span className={cn("flex size-6 items-center justify-center text-xs font-medium", isToday ? "bg-primary text-primary-foreground" : "text-muted-foreground", recordsRadiusClassName(radius, "pill"))}>
                  {day.getDate()}
                </span>
                {dayRecords.length > 0 ? (
                  <span className="text-[11px] text-muted-foreground">{dayRecords.length}</span>
                ) : null}
              </div>
              <div className="flex flex-col gap-1">
                {dayRecords.slice(0, 4).map((record) => (
                  <RecordMiniCard
                    key={String(record[primaryKey] ?? "")}
                    record={record}
                    primaryKey={primaryKey}
                    columns={visibleColumns}
                    selectedRecords={selectedRecords}
                    selectionEnabled={selectionEnabled}
                    onSelectRecord={onSelectRecord}
                    onRecordClick={onRecordClick}
                    foreignKeyLabelMap={foreignKeyLabelMap}
                    columnLabels={columnLabels}
                    displayOptions={displayOptions}
                    quickActions={quickActions}
                    onQuickAction={onQuickAction}
                    pendingActionKey={pendingActionKey}
                    enumColorMap={enumColorMap}
                    density="compact"
                    radius={radius}
                  />
                ))}
                {dayRecords.length > 4 ? (
                  <span className="px-2 py-1 text-xs text-muted-foreground">+{dayRecords.length - 4} more</span>
                ) : null}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

export function RecordsTimelineView({
  records,
  table,
  visibleColumns,
  primaryKey,
  selectedRecords,
  selectionEnabled = true,
  onSelectRecord,
  onRecordClick,
  foreignKeyLabelMap,
  columnLabels,
  displayOptions,
  quickActions,
  onQuickAction,
  pendingActionKey,
  enumColorMap,
  appearance,
  density,
  radius,
  dateField,
}: RecordsTimelineViewProps) {
  const dateColumn = pickDateColumn(visibleColumns, table.columns, dateField)
  const sortedRecords = React.useMemo(() => {
    if (!dateColumn) return records
    return [...records].sort((a, b) => {
      const aDate = coerceDate(a[dateColumn.name])?.getTime() ?? Number.POSITIVE_INFINITY
      const bDate = coerceDate(b[dateColumn.name])?.getTime() ?? Number.POSITIVE_INFINITY
      return aDate - bDate
    })
  }, [dateColumn, records])

  if (!dateColumn) {
    return (
      <MissingViewConfig
        icon={Rows3}
        title="Pick a date field for timeline"
        description="Pass timelineField or include a DATE/DATETIME column such as due_date, starts_at, created_at, or updated_at."
        appearance={appearance}
        radius={radius}
      />
    )
  }

  return (
    <div className={cn("flex flex-col", density === "compact" ? "gap-2" : "gap-3")}>
      {sortedRecords.map((record, index) => {
        const date = coerceDate(record[dateColumn.name])
        return (
          <div key={String(record[primaryKey] ?? index)} className="grid grid-cols-[7.5rem_minmax(0,1fr)] gap-3">
            <div className="pt-1 text-right">
              <p className="text-xs font-medium text-foreground">
                {date ? date.toLocaleDateString(undefined, { month: "short", day: "numeric" }) : "No date"}
              </p>
              <p className="mt-0.5 text-[11px] text-muted-foreground">
                {date ? date.toLocaleDateString(undefined, { year: "numeric" }) : displayColumnLabel(dateColumn.name, columnLabels)}
              </p>
            </div>
            <div className="grid grid-cols-[auto_minmax(0,1fr)] gap-3">
              <div className="flex flex-col items-center">
                <span className={cn("flex size-7 items-center justify-center border border-border/50 bg-background text-muted-foreground", recordsRadiusClassName(radius, "pill"))}>
                  <Calendar className="size-3.5" />
                </span>
                {index < sortedRecords.length - 1 ? <span className="my-1 h-full min-h-8 w-px bg-border/50" /> : null}
              </div>
              <RecordMiniCard
                record={record}
                primaryKey={primaryKey}
                columns={visibleColumns}
                selectedRecords={selectedRecords}
                selectionEnabled={selectionEnabled}
                onSelectRecord={onSelectRecord}
                onRecordClick={onRecordClick}
                foreignKeyLabelMap={foreignKeyLabelMap}
                columnLabels={columnLabels}
                displayOptions={displayOptions}
                quickActions={quickActions}
                onQuickAction={onQuickAction}
                pendingActionKey={pendingActionKey}
                enumColorMap={enumColorMap}
                density={density}
                radius={radius}
              />
            </div>
          </div>
        )
      })}
    </div>
  )
}

export function RecordsMatrixView({
  records,
  table,
  visibleColumns,
  primaryKey,
  selectedRecords,
  selectionEnabled = true,
  onSelectRecord,
  onRecordClick,
  foreignKeyLabelMap,
  columnLabels,
  displayOptions,
  quickActions,
  onQuickAction,
  pendingActionKey,
  enumColorMap,
  appearance,
  density,
  radius,
  rowField,
  columnField,
}: RecordsMatrixViewProps) {
  const rowColumn = pickMatrixColumn(visibleColumns, table.columns, rowField)
  const columnColumn = pickMatrixColumn(
    visibleColumns.filter((column) => column.name !== rowColumn?.name),
    table.columns.filter((column) => column.name !== rowColumn?.name),
    columnField,
  )
  const matrix = React.useMemo(() => buildMatrix(records, rowColumn, columnColumn), [columnColumn, records, rowColumn])

  if (!rowColumn || !columnColumn) {
    return (
      <MissingViewConfig
        icon={Grid2X2}
        title="Pick two grouping fields for matrix"
        description="Pass matrixRowsBy and matrixColumnsBy, or include at least two enum, status, owner, type, or stage fields."
        appearance={appearance}
        radius={radius}
      />
    )
  }

  return (
    <div className="min-w-[56rem] overflow-auto">
      <div
        className="grid"
        style={{ gridTemplateColumns: `minmax(9rem, 12rem) repeat(${matrix.columnKeys.length}, minmax(14rem, 1fr))` }}
      >
        <div className={cn("sticky left-0 z-10 border-b border-r border-border/40 bg-card px-3 py-2", recordsRadiusClassName(radius, "surface"))}>
          <p className="text-xs font-medium text-foreground">{displayColumnLabel(rowColumn.name, columnLabels)}</p>
          <p className="text-[11px] text-muted-foreground">by {displayColumnLabel(columnColumn.name, columnLabels)}</p>
        </div>
        {matrix.columnKeys.map((columnKey) => (
          <div key={columnKey} className="border-b border-r border-border/40 bg-muted/20 px-3 py-2">
            <p className="truncate text-xs font-medium text-foreground">{columnKey}</p>
            <p className="text-[11px] text-muted-foreground">{matrix.columnTotals.get(columnKey) ?? 0} records</p>
          </div>
        ))}
        {matrix.rowKeys.map((rowKey) => (
          <React.Fragment key={rowKey}>
            <div className="sticky left-0 z-10 border-b border-r border-border/40 bg-card px-3 py-3">
              <p className="truncate text-sm font-medium text-foreground">{rowKey}</p>
              <p className="text-xs text-muted-foreground">{matrix.rowTotals.get(rowKey) ?? 0} records</p>
            </div>
            {matrix.columnKeys.map((columnKey) => {
              const cellRecords = matrix.cells.get(`${rowKey}\u0000${columnKey}`) ?? []
              return (
                <div key={`${rowKey}:${columnKey}`} className={cn("min-h-36 border-b border-r border-border/30 bg-background/30", density === "compact" ? "p-1.5" : "p-2")}>
                  {cellRecords.length === 0 ? (
                    <div className={cn("flex h-full min-h-28 items-center justify-center border border-dashed border-border/40 text-xs text-muted-foreground", recordsRadiusClassName(radius, "surface"))}>
                      Empty
                    </div>
                  ) : (
                    <div className="flex flex-col gap-1.5">
                      {cellRecords.slice(0, 5).map((record) => (
                        <RecordMiniCard
                          key={String(record[primaryKey] ?? "")}
                          record={record}
                          primaryKey={primaryKey}
                          columns={visibleColumns}
                          selectedRecords={selectedRecords}
                          selectionEnabled={selectionEnabled}
                          onSelectRecord={onSelectRecord}
                          onRecordClick={onRecordClick}
                          foreignKeyLabelMap={foreignKeyLabelMap}
                          columnLabels={columnLabels}
                          displayOptions={displayOptions}
                          quickActions={quickActions}
                          onQuickAction={onQuickAction}
                          pendingActionKey={pendingActionKey}
                          enumColorMap={enumColorMap}
                          density="compact"
                          radius={radius}
                        />
                      ))}
                      {cellRecords.length > 5 ? (
                        <span className="px-2 py-1 text-xs text-muted-foreground">+{cellRecords.length - 5} more</span>
                      ) : null}
                    </div>
                  )}
                </div>
              )
            })}
          </React.Fragment>
        ))}
      </div>
    </div>
  )
}

function RecordMiniCard({
  record,
  primaryKey,
  columns,
  selectedRecords,
  selectionEnabled = true,
  onSelectRecord,
  onRecordClick,
  foreignKeyLabelMap,
  columnLabels,
  displayOptions,
  quickActions,
  onQuickAction,
  pendingActionKey,
  enumColorMap,
  density,
  radius,
}: {
  record: Record<string, unknown>
  primaryKey: string
  columns: ColumnSchema[]
  selectedRecords: Set<string>
  selectionEnabled?: boolean
  onSelectRecord?: (id: string) => void
  onRecordClick: (record: Record<string, unknown>) => void
  foreignKeyLabelMap?: ForeignKeyLabelMap
  columnLabels?: ColumnLabelMap
  displayOptions?: RecordPreviewDisplayOptions
  quickActions?: RecordQuickAction[]
  onQuickAction?: (action: RecordQuickAction, record: Record<string, unknown>, index: number) => void
  pendingActionKey?: string | null
  enumColorMap?: EnumColorMap
  density: LemmaRecordsDensity
  radius: LemmaRecordsRadius
}) {
  const id = String(record[primaryKey] ?? "")
  const primaryColumn = pickPrimaryColumn(columns, displayOptions?.primaryField)
  const secondaryColumns = pickSecondaryColumns(columns, primaryColumn, {
    count: density === "compact" ? 2 : 3,
    fields: displayOptions?.secondaryFields,
  })
  const descriptionColumn = pickColumn(columns, displayOptions?.descriptionField)
  const selected = selectedRecords.has(id)

  return (
    <div
      role="button"
      tabIndex={0}
      className={cn(
        "group w-full border bg-card text-left transition-colors hover:bg-muted/35",
        selected ? "border-primary/35 bg-primary/5" : "border-border/40",
        recordsRadiusClassName(radius, "surface"),
        density === "compact" ? "p-2" : "p-3",
      )}
      onClick={() => onRecordClick(record)}
      onKeyDown={(event) => {
        if (event.key === "Enter" || event.key === " ") {
          event.preventDefault()
          onRecordClick(record)
        }
      }}
    >
      <span className="flex min-w-0 items-start gap-2">
        {selectionEnabled ? (
          <Checkbox
            checked={selected}
            onCheckedChange={() => onSelectRecord?.(id)}
            onClick={(event) => event.stopPropagation()}
            className="mt-0.5 size-3.5"
          />
        ) : null}
        <span className="min-w-0 flex-1">
          <span className="block truncate text-sm font-medium text-foreground">
            {formatRecordFieldValue(primaryColumn ? record[primaryColumn.name] : undefined, primaryColumn, foreignKeyLabelMap, enumColorMap)}
          </span>
          {descriptionColumn ? (
            <span className="mt-1 line-clamp-2 text-xs text-muted-foreground">
              {formatRecordPlainText(record[descriptionColumn.name])}
            </span>
          ) : null}
          <span className="mt-1.5 flex flex-wrap gap-1">
            {secondaryColumns.map((column) => {
              const value = record[column.name]
              if (value == null || value === "") return null
              return (
                <span key={column.name} className={cn("max-w-full truncate bg-muted/45 px-1.5 py-0.5 text-[11px] text-muted-foreground", recordsRadiusClassName(radius, "control"))}>
                  <span className="font-medium">{displayColumnLabel(column.name, columnLabels)}:</span>{" "}
                  {formatRecordFieldValue(value, column, foreignKeyLabelMap, enumColorMap)}
                </span>
              )
            })}
          </span>
          {quickActions?.length && onQuickAction ? (
            <RecordQuickActionButtons
              record={record}
              recordId={id}
              actions={quickActions}
              pendingActionKey={pendingActionKey}
              onRun={(action, index) => onQuickAction(action, record, index)}
              compact
              className="mt-2"
            />
          ) : null}
        </span>
      </span>
    </div>
  )
}

function MissingViewConfig({
  icon: Icon,
  title,
  description,
  appearance,
  radius,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  appearance: LemmaRecordsAppearance
  radius: LemmaRecordsRadius
}) {
  return (
    <div className={cn("flex min-h-64 flex-col items-center justify-center gap-3 border border-dashed border-border/40 px-6 py-8 text-center", appearance === "minimal" ? "bg-transparent" : "bg-muted/15", recordsRadiusClassName(radius, "surface"))}>
      <span className={cn("flex size-10 items-center justify-center border border-border/50 bg-muted/35 text-muted-foreground", recordsRadiusClassName(radius, "pill"))}>
        <Icon className="size-4" />
      </span>
      <div>
        <p className="font-medium text-foreground">{title}</p>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}

function pickDateColumn(
  visibleColumns: ColumnSchema[],
  allColumns: ColumnSchema[],
  preferredField?: string,
): ColumnSchema | undefined {
  const columns = [...visibleColumns, ...allColumns.filter((column) => !visibleColumns.some((visible) => visible.name === column.name))]
  if (preferredField) {
    const preferred = columns.find((column) => column.name === preferredField)
    if (preferred) return preferred
  }

  return (
    columns.find((column) => /due|deadline|scheduled|start|end|date|time|at$/i.test(column.name) && isDateColumn(column)) ??
    columns.find(isDateColumn)
  )
}

function pickMatrixColumn(
  visibleColumns: ColumnSchema[],
  allColumns: ColumnSchema[],
  preferredField?: string,
): ColumnSchema | undefined {
  const columns = [...visibleColumns, ...allColumns.filter((column) => !visibleColumns.some((visible) => visible.name === column.name))]
  if (preferredField) {
    const preferred = columns.find((column) => column.name === preferredField)
    if (preferred) return preferred
  }

  return (
    columns.find((column) => column.type === "ENUM" && !isSystemField(column)) ??
    columns.find((column) => /status|state|stage|priority|owner|assignee|type|category|team/i.test(column.name) && !isSystemField(column)) ??
    columns.find((column) => !isSystemField(column) && column.type !== "JSON" && column.type !== "VECTOR")
  )
}

function isDateColumn(column: ColumnSchema): boolean {
  return column.type === "DATE" || column.type === "DATETIME"
}

function coerceDate(value: unknown): Date | null {
  if (value == null || value === "") return null
  const date = value instanceof Date ? value : new Date(String(value))
  return Number.isNaN(date.getTime()) ? null : date
}

function startOfMonth(date: Date): Date {
  return new Date(date.getFullYear(), date.getMonth(), 1)
}

function addMonths(date: Date, months: number): Date {
  return new Date(date.getFullYear(), date.getMonth() + months, 1)
}

function buildMonthCells(cursor: Date): Date[] {
  const first = startOfMonth(cursor)
  const start = new Date(first)
  start.setDate(first.getDate() - first.getDay())
  return Array.from({ length: 42 }, (_, index) => {
    const day = new Date(start)
    day.setDate(start.getDate() + index)
    return day
  })
}

function dayKey(date: Date): string {
  const year = date.getFullYear()
  const month = String(date.getMonth() + 1).padStart(2, "0")
  const day = String(date.getDate()).padStart(2, "0")
  return `${year}-${month}-${day}`
}

function buildMatrix(
  records: Record<string, unknown>[],
  rowColumn?: ColumnSchema,
  columnColumn?: ColumnSchema,
) {
  const rowKeys: string[] = []
  const columnKeys: string[] = []
  const rowTotals = new Map<string, number>()
  const columnTotals = new Map<string, number>()
  const cells = new Map<string, Record<string, unknown>[]>()

  if (!rowColumn || !columnColumn) {
    return { rowKeys, columnKeys, rowTotals, columnTotals, cells }
  }

  for (const option of rowColumn.options ?? []) rowKeys.push(option)
  for (const option of columnColumn.options ?? []) columnKeys.push(option)

  for (const record of records) {
    const rowKey = matrixKey(record[rowColumn.name])
    const columnKey = matrixKey(record[columnColumn.name])
    if (!rowKeys.includes(rowKey)) rowKeys.push(rowKey)
    if (!columnKeys.includes(columnKey)) columnKeys.push(columnKey)

    rowTotals.set(rowKey, (rowTotals.get(rowKey) ?? 0) + 1)
    columnTotals.set(columnKey, (columnTotals.get(columnKey) ?? 0) + 1)

    const cellKey = `${rowKey}\u0000${columnKey}`
    const cell = cells.get(cellKey) ?? []
    cell.push(record)
    cells.set(cellKey, cell)
  }

  return { rowKeys, columnKeys, rowTotals, columnTotals, cells }
}

function matrixKey(value: unknown): string {
  if (value == null || value === "") return "Unassigned"
  return String(value)
}
