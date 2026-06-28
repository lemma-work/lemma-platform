import type { ColumnSchema } from "lemma-sdk";

const ENUM_PALETTE = [
  { bg: "bg-blue-100 dark:bg-blue-500/15", text: "text-blue-700 dark:text-blue-300" },
  { bg: "bg-emerald-100 dark:bg-emerald-500/15", text: "text-emerald-700 dark:text-emerald-300" },
  { bg: "bg-violet-100 dark:bg-violet-500/15", text: "text-violet-700 dark:text-violet-300" },
  { bg: "bg-orange-100 dark:bg-orange-500/15", text: "text-orange-700 dark:text-orange-300" },
  { bg: "bg-pink-100 dark:bg-pink-500/15", text: "text-pink-700 dark:text-pink-300" },
  { bg: "bg-indigo-100 dark:bg-indigo-500/15", text: "text-indigo-700 dark:text-indigo-300" },
  { bg: "bg-teal-100 dark:bg-teal-500/15", text: "text-teal-700 dark:text-teal-300" },
  { bg: "bg-amber-100 dark:bg-amber-500/15", text: "text-amber-700 dark:text-amber-300" },
  { bg: "bg-cyan-100 dark:bg-cyan-500/15", text: "text-cyan-700 dark:text-cyan-300" },
  { bg: "bg-rose-100 dark:bg-rose-500/15", text: "text-rose-700 dark:text-rose-300" },
] as const;

function stableIndex(value: string, count: number): number {
  let hash = 0;
  for (let i = 0; i < value.length; i++) {
    hash = ((hash << 5) - hash + value.charCodeAt(i)) | 0;
  }
  return ((Math.abs(hash) % count) + count) % count;
}

export function enumColor(optionValue: string, options: string[]) {
  const idx = options.indexOf(optionValue);
  const i = idx >= 0 ? idx % ENUM_PALETTE.length : stableIndex(optionValue, ENUM_PALETTE.length);
  return ENUM_PALETTE[i];
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

const TYPE_TINTS: Record<string, { bg: string; text: string }> = {
  TEXT: { bg: "bg-muted/45", text: "text-muted-foreground" },
  INTEGER: { bg: "bg-emerald-500/10", text: "text-emerald-700 dark:text-emerald-300" },
  FLOAT: { bg: "bg-blue-500/10", text: "text-blue-700 dark:text-blue-300" },
  BOOLEAN: { bg: "bg-primary/10", text: "text-primary" },
  DATE: { bg: "bg-amber-500/10", text: "text-amber-700 dark:text-amber-300" },
  DATETIME: { bg: "bg-amber-500/10", text: "text-amber-700 dark:text-amber-300" },
  ENUM: { bg: "bg-violet-500/10", text: "text-violet-700 dark:text-violet-300" },
  JSON: { bg: "bg-rose-500/10", text: "text-rose-700 dark:text-rose-300" },
  UUID: { bg: "bg-sky-500/10", text: "text-sky-700 dark:text-sky-300" },
  SERIAL: { bg: "bg-emerald-500/10", text: "text-emerald-700 dark:text-emerald-300" },
  VECTOR: { bg: "bg-muted/45", text: "text-muted-foreground" },
};

export function typeBadgeClasses(column: ColumnSchema): string {
  const tint = TYPE_TINTS[column.type] ?? TYPE_TINTS.TEXT;
  if (column.foreign_key) {
    return "rounded-full border border-border/50 bg-sky-500/10 px-1.5 py-0.5 text-[9px] font-medium normal-case text-sky-700 dark:text-sky-300";
  }
  return `rounded-full border border-border/50 ${tint.bg} px-1.5 py-0.5 text-[9px] font-medium normal-case ${tint.text}`;
}

export function isSystemField(column: ColumnSchema): boolean {
  return (
    column.system === true ||
    column.auto === true ||
    column.computed === true ||
    column.name === "id" ||
    column.name === "created_at" ||
    column.name === "updated_at" ||
    column.name === "creator_user_id" ||
    column.name === "sort_order"
  );
}
