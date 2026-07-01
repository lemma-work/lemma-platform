import * as React from "react"
import type { ColumnSchema } from "lemma-sdk"
import { enumPillClasses, isSystemField, type EnumColorMap } from "./records-enum-utils"

export type ForeignKeyLabelMap = Record<string, Record<string, string>>
export type ColumnLabelMap = Record<string, string>
export interface RecordPreviewDisplayOptions {
  primaryField?: string
  secondaryFields?: string[]
  descriptionField?: string
  badgeField?: string
  showFieldLabels?: boolean
}

const PRIMARY_FIELD_NAMES = ["title", "name", "label", "subject", "full_name", "primary_email"]

export function isUuidLike(value: string): boolean {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(value)
}

export function shortenIdentifier(value: unknown): string {
  const text = String(value ?? "")
  if (!text) return "—"
  if (isUuidLike(text)) return `${text.slice(0, 8)}…${text.slice(-4)}`
  return text.length > 28 ? `${text.slice(0, 24)}…` : text
}

export function displayColumnLabel(columnName: string, columnLabels?: ColumnLabelMap): string {
  return columnLabels?.[columnName] ?? columnName.replace(/_/g, " ")
}

export function pickColumn(columns: ColumnSchema[], columnName?: string): ColumnSchema | undefined {
  if (!columnName) return undefined
  return columns.find((column) => column.name === columnName)
}

export function pickPrimaryColumn(columns: ColumnSchema[], preferredField?: string): ColumnSchema | undefined {
  if (preferredField) {
    const preferred = columns.find((column) => column.name === preferredField)
    if (preferred) return preferred
  }
  return (
    columns.find((column) => PRIMARY_FIELD_NAMES.includes(column.name)) ??
    columns.find((column) => !isSystemField(column) && !column.foreign_key) ??
    columns.find((column) => !isSystemField(column)) ??
    columns[0]
  )
}

export function pickSecondaryColumns(
  columns: ColumnSchema[],
  primaryColumn?: ColumnSchema,
  options: { groupBy?: string; count?: number; fields?: string[] } = {},
): ColumnSchema[] {
  if (options.fields?.length) {
    const explicit = options.fields
      .map((fieldName) => columns.find((column) => column.name === fieldName))
      .filter((column): column is ColumnSchema => Boolean(column))
      .filter((column) => column.name !== primaryColumn?.name)
      .filter((column) => column.name !== options.groupBy)
      .filter((column) => !isSystemField(column))
      .filter((column) => column.type !== "VECTOR")
    if (explicit.length > 0) return explicit
  }

  const count = options.count ?? 4
  return columns
    .filter((column) => !isSystemField(column))
    .filter((column) => column.name !== primaryColumn?.name)
    .filter((column) => column.name !== options.groupBy)
    .filter((column) => column.type !== "VECTOR" && column.type !== "JSON")
    .slice(0, count)
}

export function formatRecordFieldValue(
  value: unknown,
  column?: ColumnSchema,
  foreignKeyLabelMap?: ForeignKeyLabelMap,
  enumColorMap?: EnumColorMap,
): React.ReactNode {
  if (value == null || value === "") return "—"

  const text = String(value)
  if (column?.foreign_key) {
    const label = foreignKeyLabelMap?.[column.name]?.[text]
    return label || shortenIdentifier(text)
  }

  if (column?.type === "ENUM" && column.options?.length) {
    return <span className={enumPillClasses(text, column.options, enumColorMap)}>{text}</span>
  }

  if (column?.type === "BOOLEAN") return value ? "Yes" : "No"

  if (column?.type === "DATE" || column?.type === "DATETIME") {
    const date = new Date(text)
    if (!Number.isNaN(date.getTime())) {
      return date.toLocaleDateString(undefined, { month: "short", day: "numeric", year: "numeric" })
    }
  }

  if (column?.type === "UUID" || isUuidLike(text)) {
    return <span title={text}>{shortenIdentifier(text)}</span>
  }

  return text
}

export function formatRecordPlainText(value: unknown): string {
  if (value == null || value === "") return ""
  if (value instanceof Date) return value.toLocaleString()
  if (Array.isArray(value)) return value.map(formatRecordPlainText).filter(Boolean).join(", ")
  if (typeof value === "object") return JSON.stringify(value)
  return String(value)
}
