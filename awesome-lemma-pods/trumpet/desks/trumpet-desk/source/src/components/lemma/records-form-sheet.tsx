"use client"

import * as React from "react"
import { Loader2 } from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/lemma/ui/dialog"
import { Sheet, SheetContent, SheetDescription, SheetHeader, SheetTitle } from "@/components/lemma/ui/sheet"
import { Separator } from "@/components/lemma/ui/separator"
import { useRecordForm } from "lemma-sdk/react"
import {
  DEFAULT_RECORD_FORM_HIDDEN_FIELDS,
  orderRecordSchemaFields,
  type LemmaClient,
  type Table,
} from "lemma-sdk"
import { type EnumColorMap } from "./records-enum-utils"
import {
  recordsRadiusClassName,
  type LemmaRecordsAppearance,
  type LemmaRecordsDensity,
  type LemmaRecordsRadius,
} from "./records-style-utils"
import { RecordFormField } from "@/components/lemma/record-form-fields"
import { cn } from "@/components/lemma/lib/utils"

type RecordFormVisibilityRule<TContext> = boolean | ((context: TContext) => boolean)
interface RecordFormFieldVisibilityContext {
  values: Record<string, unknown>
  fieldName: string
}
interface RecordFormSectionVisibilityContext {
  values: Record<string, unknown>
  label: string
  fields: string[]
}
interface RecordFormFieldGroup {
  label: string
  fields: string[]
  visible?: RecordFormVisibilityRule<RecordFormSectionVisibilityContext>
}

interface RecordFormSheetProps {
  client: LemmaClient
  podId?: string
  tableName: string
  table: Table
  recordId?: string
  submitVia?: "direct" | "function"
  submitFunctionName?: string
  hiddenFields?: string[]
  fieldOrder?: string[]
  fieldGroups?: RecordFormFieldGroup[]
  fieldVisibility?: Record<string, RecordFormVisibilityRule<RecordFormFieldVisibilityContext>>
  sectionVisibility?: Record<string, RecordFormVisibilityRule<RecordFormSectionVisibilityContext>>
  foreignKeyLabels?: Record<string, string>
  enumColorMap?: EnumColorMap
  mode?: "inline" | "modal" | "sheet"
  appearance?: LemmaRecordsAppearance
  density?: LemmaRecordsDensity
  radius?: LemmaRecordsRadius
  onClose: () => void
  onSuccess: () => void
}

export function RecordFormSheet({
  client,
  podId,
  tableName,
  table,
  recordId,
  submitVia = "direct",
  submitFunctionName,
  hiddenFields = [],
  fieldOrder,
  fieldGroups,
  fieldVisibility,
  sectionVisibility,
  foreignKeyLabels,
  mode = "sheet",
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  onClose,
  onSuccess,
}: RecordFormSheetProps) {
  const form = useRecordForm({
    client,
    podId,
    tableName,
    recordId: recordId || null,
    hiddenFields: [...hiddenFields, ...DEFAULT_RECORD_FORM_HIDDEN_FIELDS],
    submitVia,
    submitFunctionName,
    onSubmitSuccess: () => onSuccess(),
  })

  const isEdit = !!recordId
  const title = isEdit ? "Edit Record" : "New Record"

  const orderedFields = React.useMemo(() => {
    return orderRecordSchemaFields(form.editableFields, fieldOrder)
  }, [form.editableFields, fieldOrder])
  const visibleOrderedFields = React.useMemo(() => {
    return orderedFields.filter((field) =>
      resolveVisibility(fieldVisibility?.[field.name], {
        values: form.values,
        fieldName: field.name,
      }),
    )
  }, [fieldVisibility, form.values, orderedFields])
  const visibleFieldGroups = React.useMemo(() => {
    if (!fieldGroups?.length) return null

    return fieldGroups
      .filter((group) => {
        const context = { values: form.values, label: group.label, fields: group.fields }
        return resolveVisibility(group.visible, context) && resolveVisibility(sectionVisibility?.[group.label], context)
      })
      .map((group) => ({
        ...group,
        fields: group.fields.filter((fieldName) => visibleOrderedFields.some((field) => field.name === fieldName)),
      }))
      .filter((group) => group.fields.length > 0)
  }, [fieldGroups, form.values, sectionVisibility, visibleOrderedFields])

  const renderField = React.useCallback(
    (field: typeof orderedFields[number]) => (
      <RecordFormField
        key={field.name}
        field={field}
        value={form.values[field.name]}
        error={form.fieldErrors[field.name]}
        onChange={(nextValue) => form.setValue(field.name, nextValue)}
        client={client}
        podId={podId}
        tableName={tableName}
        labelField={foreignKeyLabels?.[field.name]}
        radius={radius}
        getRadiusClassName={recordsRadiusClassName}
      />
    ),
    [client, foreignKeyLabels, form, podId, radius, tableName],
  )

  const content = (
    <div className="flex h-full min-h-0 flex-col">
      {mode === "modal" ? (
        <DialogHeader className={cn("shrink-0", formHeaderClassName(appearance, density))}>
          <DialogTitle className="text-lg font-semibold tracking-tight">{title}</DialogTitle>
          <DialogDescription>{table.name}</DialogDescription>
        </DialogHeader>
      ) : (
        <SheetHeader className={cn("shrink-0", formHeaderClassName(appearance, density))}>
          <SheetTitle className="text-lg font-semibold tracking-tight">{title}</SheetTitle>
          <SheetDescription>{table.name}</SheetDescription>
        </SheetHeader>
      )}

      <div className={cn("flex-1 overflow-y-auto", formBodyClassName(density))}>
        {form.isLoadingSchema ? (
          <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading schema…
          </div>
        ) : visibleFieldGroups ? (
          <div className="space-y-6">
            {visibleFieldGroups.map((group, gi) => (
              <div key={gi}>
                <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-3">
                  {group.label}
                </p>
                <div className="space-y-4">
                  {group.fields
                    .map((fieldName) => orderedFields.find((field) => field.name === fieldName))
                    .filter((field): field is typeof orderedFields[number] => Boolean(field))
                    .map(renderField)}
                </div>
                {gi < visibleFieldGroups.length - 1 && <Separator className="mt-6 bg-border/30" />}
              </div>
            ))}
          </div>
        ) : (
          <div className="space-y-4">
            {visibleOrderedFields.map(renderField)}
          </div>
        )}
      </div>

      <div className={cn("shrink-0", formFooterClassName(appearance, density))}>
        <div className="flex items-center justify-end gap-3">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button
            onClick={() => form.submit()}
            disabled={form.isSubmitting || form.isLoadingSchema}
          >
            {form.isSubmitting && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
            {isEdit ? "Save Changes" : "Create"}
          </Button>
        </div>
      </div>
    </div>
  )

  if (mode === "sheet") {
    return (
      <Sheet open onOpenChange={(open) => !open && onClose()}>
        <SheetContent
          className={cn("!h-full !w-[calc(100vw-1rem)] !max-w-[60rem] gap-0 p-0", overlaySurfaceClassName(appearance, radius))}
        >
          {content}
        </SheetContent>
      </Sheet>
    )
  }

  if (mode === "modal") {
    return (
      <Dialog open onOpenChange={(open) => !open && onClose()}>
        <DialogContent
          className={cn("!h-[92vh] !max-h-[92vh] !w-[calc(100vw-2rem)] !max-w-[72rem] gap-0 overflow-hidden p-0", overlaySurfaceClassName(appearance, radius))}
        >
          {content}
        </DialogContent>
      </Dialog>
    )
  }

  return <div className={cn(appearance === "minimal" ? "bg-transparent" : "bg-card", overlaySurfaceClassName(appearance, radius))}>{content}</div>
}

function formHeaderClassName(appearance: LemmaRecordsAppearance, density: LemmaRecordsDensity) {
  return cn(
    appearance === "borderless" ? "border-b-0" : appearance === "minimal" ? "border-b border-border/15" : "border-b border-border/50",
    density === "compact" ? "px-4 py-3" : density === "spacious" ? "px-7 py-5" : "px-6 py-4",
  )
}

function formBodyClassName(density: LemmaRecordsDensity) {
  if (density === "compact") return "px-4 py-3"
  if (density === "spacious") return "px-7 py-6"
  return "px-6 py-4"
}

function formFooterClassName(appearance: LemmaRecordsAppearance, density: LemmaRecordsDensity) {
  return cn(
    appearance === "borderless"
      ? "border-t-0 bg-transparent"
      : appearance === "minimal"
        ? "border-t border-border/15 bg-transparent"
        : "border-t border-border/50 bg-muted/30",
    density === "compact" ? "px-4 py-2.5" : density === "spacious" ? "px-7 py-4" : "px-6 py-3",
  )
}

function overlaySurfaceClassName(appearance: LemmaRecordsAppearance, radius: LemmaRecordsRadius) {
  const radiusClassName = recordsRadiusClassName(radius, "overlay")
  if (appearance === "borderless") return cn(radiusClassName, "border-0 shadow-xl ring-0")
  if (appearance === "minimal") return cn(radiusClassName, "border-0 shadow-none ring-0")
  if (appearance === "contained") return cn(radiusClassName, "border-border/70 shadow-xl")
  return cn(radiusClassName, "border-border/50")
}

function resolveVisibility<TContext>(
  rule: RecordFormVisibilityRule<TContext> | undefined,
  context: TContext,
): boolean {
  if (typeof rule === "boolean") return rule
  if (typeof rule === "function") return rule(context)
  return true
}
