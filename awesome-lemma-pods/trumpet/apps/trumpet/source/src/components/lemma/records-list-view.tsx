import * as React from "react"
import { cn } from "@/components/lemma/lib/utils"
import { Checkbox } from "@/components/lemma/ui/checkbox"
import type { ColumnSchema, Table } from "lemma-sdk"
import { isSystemField, type EnumColorMap } from "./records-enum-utils"
import {
  displayColumnLabel,
  formatRecordPlainText,
  formatRecordFieldValue,
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
  type LemmaRecordsSurface,
} from "./records-style-utils"

interface ListViewProps {
  records: Record<string, unknown>[]
  table: Table
  visibleColumns?: ColumnSchema[]
  appearance?: LemmaRecordsAppearance
  surface?: LemmaRecordsSurface
  density?: LemmaRecordsDensity
  radius?: LemmaRecordsRadius
  selectedRecords: Set<string>
  selectionEnabled?: boolean
  onSelectRecord?: (id: string) => void
  onRecordClick: (record: Record<string, unknown>) => void
  renderCard?: (record: Record<string, unknown>, columns: ColumnSchema[]) => React.ReactNode
  foreignKeyLabelMap?: ForeignKeyLabelMap
  columnLabels?: ColumnLabelMap
  displayOptions?: RecordPreviewDisplayOptions
  quickActions?: RecordQuickAction[]
  onQuickAction?: (action: RecordQuickAction, record: Record<string, unknown>, index: number) => void
  pendingActionKey?: string | null
  enumColorMap?: EnumColorMap
}

export function ListView({
  records,
  table,
  visibleColumns,
  selectedRecords,
  selectionEnabled = true,
  onSelectRecord,
  onRecordClick,
  renderCard,
  foreignKeyLabelMap,
  columnLabels,
  displayOptions,
  quickActions,
  onQuickAction,
  pendingActionKey,
  enumColorMap,
  appearance = "default",
  surface = appearance === "borderless" ? "inherit" : appearance === "minimal" ? "muted" : "card",
  density = "comfortable",
  radius = "lg",
}: ListViewProps) {
  const pk = table.primary_key_column || "id"
  const columns = visibleColumns ?? table.columns.filter((c) => !isSystemField(c))

  const primaryCol = pickPrimaryColumn(columns, displayOptions?.primaryField)
  const secondaryCols = pickSecondaryColumns(columns, primaryCol, {
    count: 5,
    fields: displayOptions?.secondaryFields,
  })
  const descriptionCol = pickColumn(columns, displayOptions?.descriptionField)
  const badgeCol = pickColumn(columns, displayOptions?.badgeField)
  const showFieldLabels = displayOptions?.showFieldLabels ?? true

  return (
    <div className={cn("flex flex-col", density === "compact" ? "gap-1.5" : density === "spacious" ? "gap-3" : "gap-2")}>
      {records.map((record) => {
        const id = String(record[pk] ?? "")
        const selected = selectedRecords.has(id)
        return (
          <div
            key={id}
            onClick={() => onRecordClick(record)}
            onKeyDown={(event) => {
              if (event.key === "Enter" || event.key === " ") {
                event.preventDefault()
                onRecordClick(record)
              }
            }}
            role="button"
            tabIndex={0}
            className={cn(
              "group cursor-pointer border transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/40",
              selected
                ? "border-primary/30 bg-primary/5"
                : surface === "card"
                  ? "border-border/50 bg-card/85 hover:border-border/70 hover:bg-card"
                  : surface === "muted"
                    ? "border-border/15 bg-background/65 hover:border-border/30 hover:bg-background/85"
                    : "border-transparent bg-transparent hover:border-border/20 hover:bg-muted/20",
              appearance === "minimal" && (
                selected
                  ? "border-primary/25 bg-primary/5"
                  : "shadow-none"
              ),
              recordsRadiusClassName(radius, "surface"),
            )}
          >
            {renderCard ? (
              <div className="p-4">{renderCard(record, columns)}</div>
            ) : (
              <div className={cn("flex items-start gap-3 px-4", density === "compact" ? "py-2.5" : density === "spacious" ? "py-[1.125rem]" : "py-3.5")}>
                {selectionEnabled ? (
                  <Checkbox
                    checked={selected}
                    onCheckedChange={() => onSelectRecord?.(id)}
                    onClick={(e) => e.stopPropagation()}
                    className="mt-1"
                  />
                ) : null}
                <div className="min-w-0 flex-1">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <p className="min-w-0 flex-1 truncate text-sm font-medium text-foreground">
                          {formatRecordFieldValue(primaryCol ? record[primaryCol.name] : undefined, primaryCol, foreignKeyLabelMap, enumColorMap)}
                        </p>
                        {badgeCol && record[badgeCol.name] != null && record[badgeCol.name] !== "" ? (
                          <span className={cn("shrink-0", badgeCol.type === "ENUM" ? "contents" : "inline-flex items-center border border-border/50 bg-muted/40 px-2 py-0.5 text-[11px] font-medium text-foreground", badgeCol.type === "ENUM" ? undefined : recordsRadiusClassName(radius, "pill"))}>
                            {formatRecordFieldValue(record[badgeCol.name], badgeCol, foreignKeyLabelMap, enumColorMap)}
                          </span>
                        ) : null}
                      </div>
                      {descriptionCol ? (
                        (() => {
                          const description = formatRecordPlainText(record[descriptionCol.name])
                          if (!description) return null
                          return (
                            <p className="mt-1 line-clamp-2 text-sm text-muted-foreground">
                              {description}
                            </p>
                          )
                        })()
                      ) : null}
                    </div>
                  </div>
                  <div className="mt-1.5 flex flex-wrap items-center gap-x-3 gap-y-1">
                    {secondaryCols.map((col) => {
                      const val = record[col.name]
                      if (val == null || val === "") return null
                      return (
                        <span key={col.name} className="text-xs text-muted-foreground">
                          {showFieldLabels ? <span className="font-medium">{displayColumnLabel(col.name, columnLabels)}:</span> : null}
                          {showFieldLabels ? " " : null}
                          {formatRecordFieldValue(val, col, foreignKeyLabelMap, enumColorMap)}
                        </span>
                      )
                    })}
                  </div>
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
                </div>
              </div>
            )}
          </div>
        )
      })}
    </div>
  )
}
