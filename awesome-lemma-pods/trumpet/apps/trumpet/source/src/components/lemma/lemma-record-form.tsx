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
} from "lemma-sdk"
import { RecordFormField } from "@/components/lemma/record-form-fields"
import { cn } from "@/components/lemma/lib/utils"

export type LemmaRecordFormAppearance = "default" | "minimal" | "borderless" | "contained"
export type LemmaRecordFormSurface = "inherit" | "muted" | "card"
export type LemmaRecordFormDensity = "compact" | "comfortable" | "spacious"
export type LemmaRecordFormRadius = "none" | "sm" | "md" | "lg" | "xl"
export type LemmaRecordFormVisibilityRule<TContext> = boolean | ((context: TContext) => boolean)

export interface LemmaRecordFormFieldVisibilityContext {
  values: Record<string, unknown>
  fieldName: string
}

export interface LemmaRecordFormSectionVisibilityContext {
  values: Record<string, unknown>
  label: string
  fields: string[]
}

export interface LemmaRecordFormFieldGroup {
  label: string
  fields: string[]
  visible?: LemmaRecordFormVisibilityRule<LemmaRecordFormSectionVisibilityContext>
}

export interface LemmaRecordFormProps {
  client: LemmaClient
  podId?: string
  tableName: string
  recordId?: string

  mode?: "inline" | "modal" | "sheet"
  surface?: LemmaRecordFormSurface
  appearance?: LemmaRecordFormAppearance
  density?: LemmaRecordFormDensity
  radius?: LemmaRecordFormRadius
  submitVia?: "direct" | "function"
  submitFunctionName?: string
  submitFunctionInput?: (payload: Record<string, unknown>) => Record<string, unknown>
  hiddenFields?: string[]
  visibleFields?: string[]
  fieldOrder?: string[]
  fieldGroups?: LemmaRecordFormFieldGroup[]
  fieldVisibility?: Record<string, LemmaRecordFormVisibilityRule<LemmaRecordFormFieldVisibilityContext>>
  sectionVisibility?: Record<string, LemmaRecordFormVisibilityRule<LemmaRecordFormSectionVisibilityContext>>
  foreignKeyLabels?: Record<string, string>

  initialValues?: Record<string, unknown>
  onSuccess?: (record: Record<string, unknown>) => void
  onClose?: () => void
}

export function LemmaRecordForm({
  client,
  podId,
  tableName,
  recordId,
  mode = "inline",
  surface,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  submitVia = "direct",
  submitFunctionName,
  submitFunctionInput,
  hiddenFields = [],
  visibleFields,
  fieldOrder,
  fieldGroups,
  fieldVisibility,
  sectionVisibility,
  foreignKeyLabels,
  initialValues,
  onSuccess,
  onClose,
}: LemmaRecordFormProps) {
  const form = useRecordForm({
    client,
    podId,
    tableName,
    recordId: recordId || null,
    hiddenFields: [...hiddenFields, ...DEFAULT_RECORD_FORM_HIDDEN_FIELDS],
    visibleFields,
    submitVia,
    submitFunctionName,
    submitFunctionInput,
    onSubmitSuccess: (record) => onSuccess?.(record),
    initialValues,
  })

  const isEdit = !!recordId
  const title = isEdit ? "Edit Record" : "New Record"
  const resolvedSurface = surface ?? (appearance === "borderless" ? "inherit" : appearance === "minimal" ? "muted" : "card")

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
        getRadiusClassName={formRadiusClassName}
      />
    ),
    [client, foreignKeyLabels, form, podId, radius, tableName],
  )

  const inner = (
    <div className="flex h-full min-h-0 flex-col">
      {mode === "modal" && (
        <DialogHeader className={cn("shrink-0", formHeaderClassName(resolvedSurface, density))}>
          <DialogTitle className="text-lg font-semibold tracking-tight">{title}</DialogTitle>
          <DialogDescription>{tableName}</DialogDescription>
        </DialogHeader>
      )}

      {mode === "sheet" && (
        <SheetHeader className={cn("shrink-0", formHeaderClassName(resolvedSurface, density))}>
          <SheetTitle className="text-lg font-semibold tracking-tight">{title}</SheetTitle>
          <SheetDescription>{tableName}</SheetDescription>
        </SheetHeader>
      )}

      <div className={cn("flex-1 overflow-y-auto", formBodyClassName(density))}>
        {form.isLoadingSchema ? (
          <div className="flex items-center justify-center py-12 text-sm text-muted-foreground">
            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
            Loading schema…
          </div>
        ) : visibleFieldGroups ? (
          <div className={cn("flex flex-col", density === "compact" ? "gap-4" : density === "spacious" ? "gap-7" : "gap-6")}>
            {visibleFieldGroups.map((group, gi) => (
              <div key={gi}>
                <p className="text-[10px] font-semibold uppercase tracking-widest text-muted-foreground mb-3">
                  {group.label}
                </p>
                <div className={cn("flex flex-col", density === "compact" ? "gap-3" : density === "spacious" ? "gap-5" : "gap-4")}>
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
          <div className={cn("flex flex-col", density === "compact" ? "gap-3" : density === "spacious" ? "gap-5" : "gap-4")}>
            {visibleOrderedFields.map(renderField)}
          </div>
        )}

        {form.error && (
          <p className="mt-4 rounded-md border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive">
            {form.error.message}
          </p>
        )}
      </div>

      <div className={cn("shrink-0", formFooterClassName(resolvedSurface, density))}>
        <div className="flex items-center justify-end gap-3">
          {onClose && (
            <Button variant="ghost" onClick={onClose}>
              Cancel
            </Button>
          )}
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
      <Sheet open onOpenChange={(open) => !open && onClose?.()}>
        <SheetContent
          className={cn("!h-full !w-[calc(100vw-1rem)] !max-w-[60rem] gap-0 p-0", formSurfaceClassName(resolvedSurface, radius))}
        >
          {inner}
        </SheetContent>
      </Sheet>
    )
  }

  if (mode === "modal") {
    return (
      <Dialog open onOpenChange={(open) => !open && onClose?.()}>
        <DialogContent
          className={cn("!h-[92vh] !max-h-[92vh] !w-[calc(100vw-2rem)] !max-w-[72rem] gap-0 overflow-hidden p-0", formSurfaceClassName(resolvedSurface, radius))}
        >
          {inner}
        </DialogContent>
      </Dialog>
    )
  }

  return <div className={cn(resolvedSurface === "inherit" ? "bg-transparent" : resolvedSurface === "muted" ? "bg-transparent" : "bg-card", formSurfaceClassName(resolvedSurface, radius))}>{inner}</div>
}

function formHeaderClassName(surface: LemmaRecordFormSurface, density: LemmaRecordFormDensity) {
  return cn(
    surface === "inherit" ? "border-b-0" : surface === "muted" ? "border-b border-border/15" : "border-b border-border/50",
    density === "compact" ? "px-4 py-3" : density === "spacious" ? "px-7 py-5" : "px-6 py-4",
  )
}

function formBodyClassName(density: LemmaRecordFormDensity) {
  if (density === "compact") return "px-4 py-3"
  if (density === "spacious") return "px-7 py-6"
  return "px-6 py-4"
}

function formFooterClassName(surface: LemmaRecordFormSurface, density: LemmaRecordFormDensity) {
  return cn(
    surface === "inherit"
      ? "border-t-0 bg-transparent"
      : surface === "muted"
        ? "border-t border-border/15 bg-transparent"
        : "border-t border-border/50 bg-muted/30",
    density === "compact" ? "px-4 py-2.5" : density === "spacious" ? "px-7 py-4" : "px-6 py-3",
  )
}

function formSurfaceClassName(surface: LemmaRecordFormSurface, radius: LemmaRecordFormRadius) {
  const radiusClassName = formRadiusClassName(radius, "surface")
  if (surface === "inherit") return cn(radiusClassName, "border-0 bg-transparent shadow-none ring-0")
  if (surface === "muted") return cn(radiusClassName, "border-0 bg-background/70 shadow-none ring-1 ring-border/15")
  return cn(radiusClassName, "border border-border/50")
}

function resolveVisibility<TContext>(
  rule: LemmaRecordFormVisibilityRule<TContext> | undefined,
  context: TContext,
): boolean {
  if (typeof rule === "boolean") return rule
  if (typeof rule === "function") return rule(context)
  return true
}

function formRadiusClassName(
  radius: LemmaRecordFormRadius = "lg",
  target: "surface" | "control" | "pill" = "surface",
): string {
  if (radius === "none") return "rounded-none"
  if (radius === "sm") return target === "surface" ? "rounded-md" : "rounded-sm"
  if (radius === "md") return "rounded-md"
  if (radius === "xl") return target === "surface" ? "rounded-2xl" : target === "control" ? "rounded-xl" : "rounded-full"
  return target === "surface" ? "rounded-xl" : target === "control" ? "rounded-lg" : "rounded-full"
}
