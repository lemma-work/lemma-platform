"use client"

import * as React from "react"
import { ChevronLeft, ChevronRight } from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import { Dialog, DialogContent } from "@/components/lemma/ui/dialog"
import { Sheet, SheetContent } from "@/components/lemma/ui/sheet"
import type { LemmaClient, Table } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import {
  RecordDetail,
  type RecordDetailFieldGroup,
  type RecordDetailRelatedRecord,
  type RecordDetailSectionVisibilityRule,
  type RecordDetailTab,
  type RecordDetailVariant,
} from "./records-detail"
import { type EnumColorMap } from "./records-enum-utils"
import type { ColumnLabelMap } from "./records-display-utils"
import {
  recordsRadiusClassName,
  type LemmaRecordsAppearance,
  type LemmaRecordsDensity,
  type LemmaRecordsRadius,
} from "./records-style-utils"

export interface DetailSheetProps {
  record: Record<string, unknown>
  table: Table
  client: LemmaClient
  podId?: string
  mode?: "sheet" | "modal"
  variant?: RecordDetailVariant
  tabs?: RecordDetailTab[]
  headerFields?: string[]
  fieldGroups?: RecordDetailFieldGroup[]
  relatedRecords?: RecordDetailRelatedRecord[]
  editable?: boolean
  hiddenFields?: string[]
  titleField?: string
  descriptionField?: string
  identifierField?: string
  statusField?: string
  onClose: () => void
  onRecordChanged: () => void
  onDelete: () => void
  onNext?: () => void
  onPrevious?: () => void
  hasPrevious?: boolean
  hasNext?: boolean
  updateVia?: "direct" | "function"
  updateFunctionName?: string
  columnLabels?: ColumnLabelMap
  foreignKeyLabels?: Record<string, string>
  enumColorMap?: EnumColorMap
  appearance?: LemmaRecordsAppearance
  density?: LemmaRecordsDensity
  radius?: LemmaRecordsRadius
  actions?: React.ReactNode
  sectionLabels?: Partial<Record<string, React.ReactNode>>
  sectionVisibility?: Partial<Record<string, RecordDetailSectionVisibilityRule>>
  tableLabel?: React.ReactNode
}

export function DetailSheet({
  record,
  table,
  client,
  podId,
  mode = "sheet",
  variant = "workspace",
  tabs,
  headerFields,
  fieldGroups,
  relatedRecords,
  editable = true,
  hiddenFields,
  titleField,
  descriptionField,
  identifierField,
  statusField,
  onClose,
  onRecordChanged,
  onDelete,
  onNext,
  onPrevious,
  hasPrevious,
  hasNext,
  updateVia,
  updateFunctionName,
  columnLabels,
  foreignKeyLabels,
  enumColorMap,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  actions,
  sectionLabels,
  sectionVisibility,
  tableLabel,
}: DetailSheetProps) {
  const content = (
    <RecordDetail
      record={record}
      table={table}
      client={client}
      podId={podId}
      mode={editable ? "editable" : "view"}
      variant={variant}
      tabs={tabs}
      headerFields={headerFields}
      fieldGroups={fieldGroups}
      relatedRecords={relatedRecords}
      hiddenFields={hiddenFields}
      titleField={titleField}
      descriptionField={descriptionField}
      identifierField={identifierField}
      statusField={statusField}
      updateVia={updateVia}
      updateFunctionName={updateFunctionName}
      columnLabels={columnLabels}
      foreignKeyLabels={foreignKeyLabels}
      enumColorMap={enumColorMap}
      appearance={appearance}
      density={density}
      radius={radius}
      tableLabel={tableLabel}
      sectionLabels={sectionLabels}
      sectionVisibility={sectionVisibility}
      onRecordChanged={onRecordChanged}
      onDelete={onDelete}
      layout="embedded"
      className="h-full border-0 shadow-none"
      actions={
        <div className="flex min-w-0 flex-wrap items-center gap-2">
          {actions ? (
            <div className="min-w-0 max-w-full flex-1 overflow-hidden">
              {actions}
            </div>
          ) : null}
          <div className="flex shrink-0 items-center gap-1">
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={onPrevious}
              disabled={!hasPrevious}
            >
              <ChevronLeft />
              <span className="sr-only">Previous record</span>
            </Button>
            <Button
              type="button"
              variant="ghost"
              size="icon-sm"
              onClick={onNext}
              disabled={!hasNext}
            >
              <ChevronRight />
              <span className="sr-only">Next record</span>
            </Button>
          </div>
        </div>
      }
    />
  )

  if (mode === "modal") {
    return (
      <Dialog open onOpenChange={(open) => !open && onClose()}>
        <DialogContent
          className={cn(
            "!h-[92vh] !max-h-[92vh] !w-[calc(100vw-2rem)] !max-w-[82rem] gap-0 overflow-hidden p-0",
            detailOverlayClassName(appearance, radius),
          )}
        >
          {content}
        </DialogContent>
      </Dialog>
    )
  }

  return (
    <Sheet open onOpenChange={(open) => !open && onClose()}>
      <SheetContent
        className={cn(
          "!w-[calc(100vw-1rem)] !max-w-[64rem] gap-0 overflow-hidden p-0",
          detailOverlayClassName(appearance, radius),
        )}
      >
        {content}
      </SheetContent>
    </Sheet>
  )
}
function detailOverlayClassName(appearance: LemmaRecordsAppearance, radius: LemmaRecordsRadius) {
  return cn(
    recordsRadiusClassName(radius, "overlay"),
    appearance === "minimal" ? "border-0 bg-background shadow-none" : null,
    appearance === "borderless" ? "border-0 bg-background shadow-xl" : null,
    appearance === "contained" ? "border-border/70 bg-card shadow-xl" : null,
    appearance === "default" ? "border-border/50 bg-card" : null,
  )
}
