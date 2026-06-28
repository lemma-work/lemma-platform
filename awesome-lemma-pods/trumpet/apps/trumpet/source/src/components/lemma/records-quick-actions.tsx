import * as React from "react"
import { Button } from "@/components/lemma/ui/button"
import { cn } from "@/components/lemma/lib/utils"

export type RecordActionMode = "direct" | "function" | "workflow"
export type RecordActionPlacement = "detail" | "preview" | "both"
export type RecordActionScope = "row" | "detail" | "bulk"

export interface RecordActionRunContext {
  tableName: string
  scope: RecordActionScope
  record?: Record<string, unknown>
  records?: Record<string, unknown>[]
  recordId?: string
  recordIds?: string[]
}

export interface RecordAction {
  id?: string
  label: string
  icon?: React.ComponentType<{ className?: string }>
  variant?: "default" | "outline" | "destructive" | "ghost"
  mode?: RecordActionMode
  functionName?: string
  workflowName?: string
  nextValues?: Record<string, unknown> | ((record: Record<string, unknown>) => Record<string, unknown>)
  buildUpdate?: (record: Record<string, unknown>) => Record<string, unknown>
  buildInput?: (record: Record<string, unknown>, context: RecordActionRunContext) => Record<string, unknown>
  buildBulkInput?: (records: Record<string, unknown>[], context: RecordActionRunContext) => Record<string, unknown>
  visible?: (record: Record<string, unknown>) => boolean
  disabled?: (record: Record<string, unknown>) => boolean
}

export interface RecordActionContext extends RecordActionRunContext {
  action: RecordAction
}

export type RecordQuickActionMode = RecordActionMode
export type RecordQuickActionPlacement = RecordActionPlacement
export type RecordQuickAction = RecordAction
export type RecordQuickActionContext = RecordActionContext

export function recordActionKey(action: RecordAction, recordId: string, index: number): string {
  return `${recordId}:${action.id ?? action.workflowName ?? action.functionName ?? action.label}:${index}`
}

export function recordQuickActionKey(action: RecordAction, recordId: string, index: number): string {
  return recordActionKey(action, recordId, index)
}

export function resolveRecordActionMode(action: RecordAction, fallbackMode?: RecordActionMode): RecordActionMode {
  return action.mode ?? fallbackMode ?? (action.workflowName ? "workflow" : action.functionName ? "function" : "direct")
}

export function resolveRecordActionValues(
  action: RecordAction,
  record: Record<string, unknown>,
): Record<string, unknown> {
  const nextValues = typeof action.nextValues === "function"
    ? action.nextValues(record)
    : action.nextValues
  return {
    ...(nextValues ?? {}),
    ...(action.buildUpdate?.(record) ?? {}),
  }
}

export function resolveQuickActionValues(
  action: RecordAction,
  record: Record<string, unknown>,
): Record<string, unknown> {
  return resolveRecordActionValues(action, record)
}

export function createRecordActionInput(
  action: RecordAction,
  context: RecordActionRunContext,
): Record<string, unknown> {
  if (context.scope === "bulk") {
    return {
      record_ids: context.recordIds ?? [],
      records: context.records ?? [],
      ...(action.buildBulkInput?.(context.records ?? [], context) ?? {}),
    }
  }

  const record = context.record ?? {}
  return {
    id: context.recordId,
    record_id: context.recordId,
    record,
    ...resolveRecordActionValues(action, record),
    ...(action.buildInput?.(record, context) ?? {}),
  }
}

export function RecordActionButtons({
  record,
  recordId,
  actions,
  pendingActionKey,
  onRun,
  compact = false,
  className,
}: {
  record: Record<string, unknown>
  recordId?: string
  actions: RecordAction[]
  pendingActionKey?: string | null
  onRun: (action: RecordAction, index: number, event?: React.MouseEvent) => void
  compact?: boolean
  className?: string
}) {
  const visibleActions = actions.filter((action) => action.visible?.(record) !== false)

  if (visibleActions.length === 0) return null

  return (
    <div className={cn("flex flex-wrap items-center gap-1.5", className)}>
      {visibleActions.map((action, index) => {
        const key = recordActionKey(action, recordId ?? String(record.id ?? ""), index)
        const Icon = action.icon
        const isPending = pendingActionKey === key
        return (
          <Button
            key={key}
            type="button"
            size={compact ? "sm" : "sm"}
            variant={action.variant ?? "outline"}
            className={cn(compact ? "h-7 px-2 text-[11px]" : "h-8 text-xs")}
            disabled={isPending || action.disabled?.(record)}
            onClick={(event) => {
              event.stopPropagation()
              onRun(action, index, event)
            }}
          >
            {Icon ? <Icon className={compact ? "mr-1 size-3.5" : "mr-1.5 size-3.5"} /> : null}
            {action.label}
          </Button>
        )
      })}
    </div>
  )
}

export const RecordQuickActionButtons = RecordActionButtons
