"use client"

import * as React from "react"
import { Check, ChevronsUpDown, Search, X } from "lucide-react"
import { Checkbox } from "@/components/lemma/ui/checkbox"
import { Input } from "@/components/lemma/ui/input"
import { Label } from "@/components/lemma/ui/label"
import { Popover, PopoverContent, PopoverTrigger } from "@/components/lemma/ui/popover"
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/lemma/ui/select"
import { Textarea } from "@/components/lemma/ui/textarea"
import { useForeignKeyOptions } from "lemma-sdk/react"
import type { ColumnSchema, LemmaClient } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"

export type SharedRecordFormRadius = "none" | "sm" | "md" | "lg" | "xl"

export interface SharedRecordFormFieldDescriptor {
  name: string
  label: string
  kind: string
  column: ColumnSchema
  required?: boolean
  options?: string[]
}

export interface RecordFormFieldProps {
  field: SharedRecordFormFieldDescriptor
  value: unknown
  error?: string
  onChange: (value: unknown) => void
  client: LemmaClient
  podId?: string
  tableName: string
  labelField?: string
  radius?: SharedRecordFormRadius
  getRadiusClassName?: (radius: SharedRecordFormRadius, target: "surface" | "control" | "pill") => string
}

export interface RecordFieldInputProps {
  field: SharedRecordFormFieldDescriptor
  value: unknown
  onChange: (value: unknown) => void
  client: LemmaClient
  podId?: string
  tableName: string
  labelField?: string
  radius?: SharedRecordFormRadius
  getRadiusClassName?: (radius: SharedRecordFormRadius, target: "surface" | "control" | "pill") => string
  controlSize?: "sm" | "md"
  controlClassName?: string
  disabled?: boolean
  onBlur?: React.FocusEventHandler<HTMLInputElement | HTMLTextAreaElement>
  onKeyDown?: React.KeyboardEventHandler<HTMLInputElement | HTMLTextAreaElement>
}

export function RecordFormField({
  field,
  value,
  error,
  onChange,
  client,
  podId,
  tableName,
  labelField,
  radius = "lg",
  getRadiusClassName = defaultRadiusClassName,
}: RecordFormFieldProps) {
  const displayLabel = field.label || field.name.replace(/_/g, " ")

  return (
    <div className="space-y-1.5">
      <div className="flex items-center gap-2">
        <Label className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
          {displayLabel}
          {field.required && <span className="ml-0.5 text-destructive">*</span>}
        </Label>
        <span className={typeBadgeClassName(field.column, radius, getRadiusClassName)}>
          {field.column.foreign_key ? "ref" : field.column.type.toLowerCase()}
        </span>
      </div>

      <RecordFieldInput
        field={field}
        value={value}
        onChange={onChange}
        client={client}
        podId={podId}
        tableName={tableName}
        labelField={labelField}
        radius={radius}
        getRadiusClassName={getRadiusClassName}
      />

      {error ? <p className="text-xs text-destructive">{error}</p> : null}
    </div>
  )
}

export function RecordFieldInput({
  field,
  value,
  onChange,
  client,
  podId,
  tableName,
  labelField,
  radius = "lg",
  getRadiusClassName = defaultRadiusClassName,
  controlSize = "md",
  controlClassName,
  disabled,
  onBlur,
  onKeyDown,
}: RecordFieldInputProps) {
  const fkOptions = useForeignKeyOptions({
    client,
    podId,
    tableName,
    columnName: field.name,
    labelField,
    enabled: field.kind === "foreign-key",
  })

  const displayLabel = field.label || field.name.replace(/_/g, " ")
  const rawValue = value
  const stringValue = resolveFieldStringValue(value)
  const selectedForeignKeyLabel = resolveFieldOptionLabel(value)
    ?? fkOptions.options.find((opt) => String(opt.value) === stringValue)?.label
  const controlBaseClassName = buildControlClassName(controlSize, radius, getRadiusClassName, controlClassName)
  const textAreaRows = field.kind === "json" ? 4 : 3

  if (field.kind === "foreign-key") {
    return (
      <SearchableValueSelect
        value={stringValue}
        selectedLabel={selectedForeignKeyLabel}
        options={fkOptions.options}
        placeholder="Select…"
        searchPlaceholder={`Search ${displayLabel.toLowerCase()}...`}
        radius={radius}
        getRadiusClassName={getRadiusClassName}
        controlSize={controlSize}
        controlClassName={controlClassName}
        disabled={disabled}
        onChange={(nextValue) => onChange(nextValue || null)}
      />
    )
  }

  if (field.kind === "select" && field.options?.length) {
    return (
      <Select value={stringValue || undefined} onValueChange={(nextValue) => onChange(nextValue)} disabled={disabled}>
        <SelectTrigger className={controlBaseClassName}>
          <SelectValue placeholder="Select…" />
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {field.options.map((option) => (
              <SelectItem key={option} value={option}>{option}</SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    )
  }

  if (field.kind === "boolean") {
    return (
      <div className={cn("flex items-center gap-2", controlSize === "sm" ? "h-8" : "h-9")}>
        <Checkbox checked={isCheckedValue(rawValue)} disabled={disabled} onCheckedChange={(checked) => onChange(checked === true)} />
        <span className="text-sm text-muted-foreground">{isCheckedValue(rawValue) ? "Yes" : "No"}</span>
      </div>
    )
  }

  if (field.kind === "textarea") {
    return (
      <Textarea
        value={stringValue}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        rows={textAreaRows}
        className={cn("resize-none placeholder:text-muted-foreground focus-ring", controlBaseClassName)}
      />
    )
  }

  if (field.kind === "number") {
    return (
      <Input
        type="number"
        value={stringValue}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        className={cn("placeholder:text-muted-foreground focus-ring", controlBaseClassName)}
      />
    )
  }

  if (field.kind === "date") {
    return (
      <Input
        type="date"
        value={stringValue}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value || null)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        className={cn("placeholder:text-muted-foreground focus-ring", controlBaseClassName)}
      />
    )
  }

  if (field.kind === "datetime") {
    return (
      <Input
        type="datetime-local"
        value={stringValue}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value || null)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        className={cn("placeholder:text-muted-foreground focus-ring", controlBaseClassName)}
      />
    )
  }

  if (field.kind === "json") {
    return (
      <Textarea
        value={stringValue}
        disabled={disabled}
        onChange={(event) => onChange(event.target.value)}
        onBlur={onBlur}
        onKeyDown={onKeyDown}
        rows={textAreaRows}
        className={cn("resize-none font-mono text-xs placeholder:text-muted-foreground focus-ring", controlBaseClassName)}
        placeholder="{}"
      />
    )
  }

  return (
    <Input
      type="text"
      value={stringValue}
      disabled={disabled}
      onChange={(event) => onChange(event.target.value)}
      onBlur={onBlur}
      onKeyDown={onKeyDown}
      className={cn("placeholder:text-muted-foreground focus-ring", controlBaseClassName)}
      placeholder={displayLabel}
    />
  )
}

function SearchableValueSelect({
  value,
  selectedLabel,
  options,
  placeholder,
  searchPlaceholder,
  radius,
  getRadiusClassName,
  controlSize,
  controlClassName,
  disabled,
  onChange,
}: {
  value: string
  selectedLabel?: string
  options: Array<{ value: unknown; label: string }>
  placeholder: string
  searchPlaceholder: string
  radius: SharedRecordFormRadius
  getRadiusClassName: (radius: SharedRecordFormRadius, target: "surface" | "control" | "pill") => string
  controlSize: "sm" | "md"
  controlClassName?: string
  disabled?: boolean
  onChange: (value: string | null) => void
}) {
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState("")

  const filteredOptions = React.useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return options
    return options.filter((option) =>
      option.label.toLowerCase().includes(needle) || String(option.value).toLowerCase().includes(needle),
    )
  }, [options, query])

  return (
    <Popover
      open={open}
      onOpenChange={(nextOpen) => {
        setOpen(nextOpen)
        if (!nextOpen) setQuery("")
      }}
    >
      <PopoverTrigger
        type="button"
        disabled={disabled}
        className={cn(
          "inline-flex w-full items-center justify-between gap-3 px-3 text-sm transition-colors hover:bg-muted disabled:pointer-events-none disabled:opacity-60",
          buildControlClassName(controlSize, radius, getRadiusClassName, controlClassName),
        )}
      >
        <span className="min-w-0 flex-1 truncate text-left">
          {selectedLabel ?? (value ? shortenIdentifier(value) : <span className="text-muted-foreground">{placeholder}</span>)}
        </span>
        <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent align="start" className={cn("w-[var(--radix-popper-anchor-width)] min-w-72 p-0", getRadiusClassName(radius, "surface"))}>
        <div className="border-b border-border/40 p-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={searchPlaceholder}
              className={cn("pl-8 text-xs", buildControlClassName("sm", radius, getRadiusClassName, controlClassName))}
            />
          </div>
        </div>
        <div className="max-h-72 overflow-auto p-1">
          {value ? (
            <button
              type="button"
              className={cn("flex w-full items-center gap-2 px-2 py-2 text-left text-sm text-muted-foreground hover:bg-muted/45", getRadiusClassName(radius, "control"))}
              onClick={() => {
                onChange(null)
                setOpen(false)
                setQuery("")
              }}
            >
              <X className="size-4" />
              Clear selection
            </button>
          ) : null}
          {filteredOptions.length === 0 ? (
            <div className="flex min-h-24 items-center justify-center text-sm text-muted-foreground">
              No options found
            </div>
          ) : (
            filteredOptions.map((option) => {
              const selected = String(option.value) === value
              return (
                <button
                  key={String(option.value)}
                  type="button"
                  className={cn("flex w-full items-center gap-2 px-2 py-2 text-left text-sm hover:bg-muted/45", getRadiusClassName(radius, "control"), selected ? "bg-muted/60" : null)}
                  onClick={() => {
                    onChange(String(option.value))
                    setOpen(false)
                    setQuery("")
                  }}
                >
                  <span className="min-w-0 flex-1 truncate">{option.label}</span>
                  {selected ? <Check className="size-4 text-primary" /> : null}
                </button>
              )
            })
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}

function typeBadgeClassName(
  column: ColumnSchema,
  radius: SharedRecordFormRadius,
  getRadiusClassName: (radius: SharedRecordFormRadius, target: "surface" | "control" | "pill") => string,
) {
  const typeTints: Record<string, { bg: string; text: string }> = {
    TEXT: { bg: "bg-muted/45", text: "text-muted-foreground" },
    INTEGER: { bg: "bg-emerald-500/10", text: "text-emerald-700 dark:text-emerald-300" },
    FLOAT: { bg: "bg-blue-500/10", text: "text-blue-700 dark:text-blue-300" },
    BOOLEAN: { bg: "bg-primary/10", text: "text-primary" },
    DATE: { bg: "bg-amber-500/10", text: "text-amber-700 dark:text-amber-300" },
    DATETIME: { bg: "bg-amber-500/10", text: "text-amber-700 dark:text-amber-300" },
    ENUM: { bg: "bg-violet-500/10", text: "text-violet-700 dark:text-violet-300" },
    JSON: { bg: "bg-rose-500/10", text: "text-rose-700 dark:text-rose-300" },
    UUID: { bg: "bg-sky-500/10", text: "text-sky-700 dark:text-sky-300" },
  }

  const tint = column.foreign_key
    ? { bg: "bg-sky-500/10", text: "text-sky-700 dark:text-sky-300" }
    : typeTints[column.type] ?? typeTints.TEXT

  return cn(
    "border border-border/50 px-1.5 py-0.5 text-[9px] font-medium normal-case",
    tint.bg,
    tint.text,
    getRadiusClassName(radius, "pill"),
  )
}

function resolveFieldStringValue(value: unknown): string {
  if (value == null) return ""
  if (typeof value === "object" && value !== null && !Array.isArray(value)) {
    const objectValue = value as Record<string, unknown>
    if (objectValue.id != null) return String(objectValue.id)
  }
  return String(value)
}

function resolveFieldOptionLabel(value: unknown): string | undefined {
  if (typeof value !== "object" || value == null || Array.isArray(value)) return undefined

  const objectValue = value as Record<string, unknown>
  const label = objectValue.name ?? objectValue.title ?? objectValue.label
  return label != null ? String(label) : undefined
}

function isCheckedValue(value: unknown): boolean {
  return value === true || value === "true" || value === 1 || value === "1"
}

function shortenIdentifier(value: unknown): string {
  const text = String(value ?? "")
  if (!text) return "—"
  if (/^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(text)) {
    return `${text.slice(0, 8)}…${text.slice(-4)}`
  }
  return text.length > 28 ? `${text.slice(0, 24)}…` : text
}

function defaultRadiusClassName(
  radius: SharedRecordFormRadius = "lg",
  target: "surface" | "control" | "pill" = "surface",
): string {
  if (radius === "none") return "rounded-none"
  if (radius === "sm") return target === "surface" ? "rounded-md" : "rounded-sm"
  if (radius === "md") return "rounded-md"
  if (radius === "xl") return target === "surface" ? "rounded-2xl" : target === "control" ? "rounded-xl" : "rounded-full"
  return target === "surface" ? "rounded-xl" : target === "control" ? "rounded-lg" : "rounded-full"
}

function buildControlClassName(
  controlSize: "sm" | "md",
  radius: SharedRecordFormRadius,
  getRadiusClassName: (radius: SharedRecordFormRadius, target: "surface" | "control" | "pill") => string,
  controlClassName?: string,
) {
  return cn(
    controlSize === "sm" ? "h-8" : "h-9",
    "border-border bg-background",
    getRadiusClassName(radius, "control"),
    controlClassName,
  )
}
