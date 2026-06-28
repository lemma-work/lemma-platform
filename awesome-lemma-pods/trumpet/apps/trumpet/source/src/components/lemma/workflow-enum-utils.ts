const ENUM_PALETTE = [
  { bg: "bg-blue-100 dark:bg-blue-500/15", text: "text-blue-700 dark:text-blue-300" },
  { bg: "bg-emerald-100 dark:bg-emerald-500/15", text: "text-emerald-700 dark:text-emerald-300" },
  { bg: "bg-violet-100 dark:bg-violet-500/15", text: "text-violet-700 dark:text-violet-300" },
  { bg: "bg-orange-100 dark:bg-orange-500/15", text: "text-orange-700 dark:text-orange-300" },
  { bg: "bg-pink-100 dark:bg-pink-500/15", text: "text-pink-700 dark:text-pink-300" },
  { bg: "bg-indigo-100 dark:bg-indigo-500/15", text: "text-indigo-700 dark:text-indigo-300" },
  { bg: "bg-teal-100 dark:bg-teal-500/15", text: "text-teal-700 dark:text-teal-300" },
  { bg: "bg-amber-100 dark:bg-amber-500/15", text: "text-amber-700 dark:text-amber-300" },
] as const

function stableIndex(value: string, count: number): number {
  let hash = 0
  for (let i = 0; i < value.length; i++) {
    hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0
  }
  return ((Math.abs(hash) % count) + count) % count
}

export function enumColor(optionValue: string, options: string[]) {
  const idx = options.indexOf(optionValue)
  const i = idx >= 0 ? idx % ENUM_PALETTE.length : stableIndex(optionValue, ENUM_PALETTE.length)
  return ENUM_PALETTE[i]
}

export type EnumColorEntry = { bg: string; text: string }
export type EnumColorMap = Record<string, EnumColorEntry>

export function enumPillClasses(optionValue: string, options: string[], colorMap?: EnumColorMap): string {
  if (colorMap?.[optionValue]) {
    const c = colorMap[optionValue]
    return `inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${c.bg} ${c.text}`
  }
  const c = enumColor(optionValue, options)
  return `inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${c.bg} ${c.text}`
}

export type WorkflowRunStatus = "pending" | "running" | "completed" | "failed"
export type WorkflowStepStatus = "waiting" | "active" | "completed" | "failed"

const RUN_STATUS_CLASSES: Record<WorkflowRunStatus, { bg: string; text: string; pulse?: boolean }> = {
  pending: { bg: "bg-amber-100 dark:bg-amber-500/15", text: "text-amber-700 dark:text-amber-300" },
  running: { bg: "bg-blue-100 dark:bg-blue-500/15", text: "text-blue-700 dark:text-blue-300", pulse: true },
  completed: { bg: "bg-emerald-100 dark:bg-emerald-500/15", text: "text-emerald-700 dark:text-emerald-300" },
  failed: { bg: "bg-red-100 dark:bg-red-500/15", text: "text-red-700 dark:text-red-300" },
}

const STEP_STATUS_CLASSES: Record<WorkflowStepStatus, { bg: string; text: string; pulse?: boolean }> = {
  waiting: { bg: "bg-amber-100 dark:bg-amber-500/15", text: "text-amber-700 dark:text-amber-300" },
  active: { bg: "bg-blue-100 dark:bg-blue-500/15", text: "text-blue-700 dark:text-blue-300", pulse: true },
  completed: { bg: "bg-emerald-100 dark:bg-emerald-500/15", text: "text-emerald-700 dark:text-emerald-300" },
  failed: { bg: "bg-red-100 dark:bg-red-500/15", text: "text-red-700 dark:text-red-300" },
}

export function runStatusClasses(status: WorkflowRunStatus): string {
  const s = RUN_STATUS_CLASSES[status]
  const pulse = s.pulse ? "animate-pulse" : ""
  return `inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${s.bg} ${s.text} ${pulse}`
}

export function stepStatusClasses(status: WorkflowStepStatus): string {
  const s = STEP_STATUS_CLASSES[status]
  const pulse = s.pulse ? "animate-pulse" : ""
  return `inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium ${s.bg} ${s.text} ${pulse}`
}

export function toRunStatus(value: unknown): WorkflowRunStatus {
  const s = String(value ?? "").toLowerCase()
  if (s === "running" || s === "in_progress" || s === "in-progress") return "running"
  if (s === "completed" || s === "done" || s === "success" || s === "succeeded") return "completed"
  if (s === "failed" || s === "error" || s === "errored") return "failed"
  return "pending"
}

export function toStepStatus(value: unknown): WorkflowStepStatus {
  const s = String(value ?? "").toLowerCase()
  if (s === "active" || s === "in_progress" || s === "in-progress" || s === "running") return "active"
  if (s === "completed" || s === "done" || s === "success" || s === "succeeded") return "completed"
  if (s === "failed" || s === "error" || s === "errored") return "failed"
  return "waiting"
}
