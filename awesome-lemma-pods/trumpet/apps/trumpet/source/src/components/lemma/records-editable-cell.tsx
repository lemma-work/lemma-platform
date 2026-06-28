"use client"

import * as React from "react"
import { Check, Loader2 } from "lucide-react"
import { Input } from "@/components/lemma/ui/input"
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/lemma/ui/select"
import { Checkbox } from "@/components/lemma/ui/checkbox"
import { Textarea } from "@/components/lemma/ui/textarea"
import type { ColumnSchema } from "lemma-sdk"
import { enumPillClasses, type EnumColorMap } from "./records-enum-utils"
import { shortenIdentifier } from "./records-display-utils"

interface EditableCellProps {
  value: unknown
  column: ColumnSchema
  onSave: (value: unknown) => Promise<void>
  readOnly?: boolean
  foreignKeyLabelMap?: Record<string, string>
  enumColorMap?: EnumColorMap
}

export function EditableCell({ value, column, onSave, readOnly, foreignKeyLabelMap, enumColorMap }: EditableCellProps) {
  const [editing, setEditing] = React.useState(false)
  const [draft, setDraft] = React.useState<string>(serialize(value, column.type))
  const [saving, setSaving] = React.useState(false)
  const inputRef = React.useRef<HTMLInputElement | HTMLTextAreaElement | null>(null)

  React.useEffect(() => {
    if (editing && inputRef.current) inputRef.current.focus()
  }, [editing])

  const startEdit = () => {
    if (readOnly) return
    setDraft(serialize(value, column.type))
    setEditing(true)
  }

  const save = async () => {
    const next = deserialize(draft, column)
    if (next === value && column.type !== "BOOLEAN") {
      setEditing(false)
      return
    }
    setSaving(true)
    try {
      await onSave(next)
      setEditing(false)
    } finally {
      setSaving(false)
    }
  }

  const cancel = () => {
    setDraft(serialize(value, column.type))
    setEditing(false)
  }

  const handleKeyDown = (e: React.KeyboardEvent) => {
    if (e.key === "Enter" && !e.shiftKey && column.type !== "TEXT") save()
    if (e.key === "Escape") cancel()
  }

  if (readOnly) {
    return <span className="px-2 py-1.5 text-sm text-muted-foreground">{displayValue(value, column, foreignKeyLabelMap, enumColorMap)}</span>
  }

  if (column.type === "BOOLEAN") {
    return (
      <label className="flex min-h-[32px] cursor-pointer items-center gap-2 px-2 py-1.5">
        <Checkbox
          checked={Boolean(value)}
          onCheckedChange={(checked) => onSave(checked === true)}
          disabled={saving}
        />
        <span className="text-xs text-muted-foreground">{value ? "Yes" : "No"}</span>
      </label>
    )
  }

  if (column.type === "ENUM" && column.options && column.options.length) {
    const opts = column.options
    return (
      <Select
        value={String(value ?? "")}
        onValueChange={(v) => onSave(v)}
        disabled={saving}
      >
        <SelectTrigger className="h-8 gap-1 border-transparent bg-transparent px-2 text-xs shadow-none hover:border-border hover:bg-muted/50">
          {value != null ? (
            <span className={enumPillClasses(String(value), opts, enumColorMap)}>
              {String(value)}
            </span>
          ) : (
            <SelectValue placeholder="—" />
          )}
        </SelectTrigger>
        <SelectContent>
          <SelectGroup>
            {opts.map((opt) => (
              <SelectItem key={opt} value={opt}>
                <span className={enumPillClasses(opt, opts, enumColorMap)}>{opt}</span>
              </SelectItem>
            ))}
          </SelectGroup>
        </SelectContent>
      </Select>
    )
  }

  if (editing) {
    const isLong = column.type === "TEXT" && (column.max_length ?? 0) > 200
    const isJson = column.type === "JSON"

    if (isLong || isJson) {
      return (
        <div className="relative min-h-[32px] w-full px-1 py-0.5">
          <Textarea
            ref={inputRef as React.Ref<HTMLTextAreaElement>}
            value={draft}
            onChange={(e) => setDraft(e.target.value)}
            onKeyDown={handleKeyDown}
            onBlur={save}
            rows={3}
            className="min-h-[60px] resize-none border-border bg-background text-sm font-mono placeholder:text-muted-foreground focus-ring"
          />
          {saving && <Loader2 className="absolute right-2 top-2 h-3 w-3 animate-spin text-primary" />}
        </div>
      )
    }

    const inputType = column.type === "INTEGER" || column.type === "FLOAT" || column.type === "SERIAL"
      ? "number"
      : column.type === "DATE"
        ? "date"
        : column.type === "DATETIME"
          ? "datetime-local"
          : "text"

    return (
      <div className="relative min-h-[32px] w-full px-1 py-0.5">
        <Input
          ref={inputRef as React.Ref<HTMLInputElement>}
          type={inputType}
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={handleKeyDown}
          onBlur={save}
          className="h-8 border-border bg-background text-sm placeholder:text-muted-foreground focus-ring"
        />
        {saving && <Loader2 className="absolute right-2 top-2 h-3 w-3 animate-spin text-primary" />}
      </div>
    )
  }

  return (
    <div
      className="flex min-h-[32px] cursor-pointer items-center rounded-md border border-transparent px-2 py-1.5 text-sm transition-colors hover:border-border hover:bg-muted/50"
      onClick={startEdit}
    >
      {displayValue(value, column, foreignKeyLabelMap, enumColorMap)}
    </div>
  )
}

function displayValue(
  value: unknown,
  column: ColumnSchema,
  foreignKeyLabelMap?: Record<string, string>,
  enumColorMap?: EnumColorMap,
): React.ReactNode {
  if (value == null || value === "") return <span className="text-muted-foreground">—</span>
  if (column.foreign_key) {
    const text = String(value)
    return foreignKeyLabelMap?.[text] ?? <span title={text}>{shortenIdentifier(text)}</span>
  }
  if (column.type === "BOOLEAN") return value ? "Yes" : "No"
  if (column.type === "ENUM" && column.options && column.options.length) {
    return <span className={enumPillClasses(String(value), column.options, enumColorMap)}>{String(value)}</span>
  }
  if (column.type === "JSON") {
    const str = typeof value === "string" ? value : JSON.stringify(value, null, 2)
    return <span className="font-mono text-xs text-muted-foreground">{str.length > 60 ? str.slice(0, 57) + "…" : str}</span>
  }
  if (column.type === "DATE" || column.type === "DATETIME") {
    try {
      return new Date(String(value)).toLocaleDateString(undefined, { year: "numeric", month: "short", day: "numeric" })
    } catch {
      return String(value)
    }
  }
  if (column.type === "UUID" || (column.foreign_key && column.type === "TEXT")) {
    const s = String(value)
    return <span className="font-mono text-xs text-muted-foreground" title={s}>{shortenIdentifier(s)}</span>
  }
  return String(value)
}

function serialize(value: unknown, type: ColumnSchema["type"]): string {
  if (value == null) return ""
  if (type === "BOOLEAN") return value ? "true" : "false"
  if (type === "JSON") return typeof value === "string" ? value : JSON.stringify(value, null, 2)
  return String(value)
}

function deserialize(raw: string, column: ColumnSchema): unknown {
  const trimmed = raw.trim()
  if (trimmed === "") return null
  if (column.type === "INTEGER" || column.type === "SERIAL") return Number.parseInt(trimmed, 10)
  if (column.type === "FLOAT") return Number.parseFloat(trimmed)
  if (column.type === "BOOLEAN") return trimmed === "true"
  if (column.type === "JSON") {
    try { return JSON.parse(trimmed) } catch { return trimmed }
  }
  return trimmed
}
