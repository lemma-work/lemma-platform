import * as React from "react"
import { Plus, Trash2 } from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from "@/components/lemma/ui/dialog"
import { Input } from "@/components/lemma/ui/input"
import { Select, SelectContent, SelectGroup, SelectItem, SelectTrigger, SelectValue } from "@/components/lemma/ui/select"
import type { ColumnSchema, RecordFilter } from "lemma-sdk"

interface FilterBuilderProps {
  columns: ColumnSchema[]
  filters: RecordFilter[]
  onApply: (filters: RecordFilter[]) => void
  onClose: () => void
}

const OPERATORS = [
  { value: "eq", label: "is" },
  { value: "ne", label: "is not" },
  { value: "gt", label: ">" },
  { value: "gte", label: ">=" },
  { value: "lt", label: "<" },
  { value: "lte", label: "<=" },
  { value: "ilike", label: "contains" },
  { value: "starts_with", label: "starts with" },
  { value: "ends_with", label: "ends with" },
  { value: "in", label: "in" },
  { value: "is", label: "is empty" },
  { value: "is not", label: "is not empty" },
] as const

const EMPTY_VALUE = "__lemma_empty__"

function blank(columns: ColumnSchema[]): RecordFilter {
  return { field: columns[0]?.name ?? "", op: "eq", value: "" }
}

export function FilterBuilder({ columns, filters, onApply, onClose }: FilterBuilderProps) {
  const [rows, setRows] = React.useState<RecordFilter[]>(
    filters.length > 0 ? filters : [blank(columns)],
  )

  const update = <K extends keyof RecordFilter>(idx: number, key: K, val: RecordFilter[K]) => {
    setRows((prev) => {
      const next = [...prev]
      next[idx] = { ...next[idx], [key]: val }
      if (key === "field") {
        const col = columns.find((c) => c.name === val)
        if (col?.options?.length) next[idx] = { ...next[idx], op: "eq", value: "" }
      }
      return next
    })
  }

  const remove = (idx: number) => setRows((prev) => prev.filter((_, i) => i !== idx))

  const add = () => setRows((prev) => [...prev, blank(columns)])

  const apply = () => {
    onApply(
      rows.filter(
        (r) =>
          r.field &&
          (r.op === "is" || r.op === "is not" || String(r.value ?? "").trim() !== ""),
      ),
    )
    onClose()
  }

  const inputClass =
    "h-9 w-full rounded-md border border-border bg-background px-3 text-sm text-foreground placeholder:text-muted-foreground hover:border-foreground/20 focus-ring"

  const currentColumn = (field: string) => columns.find((c) => c.name === field)

  return (
    <Dialog open onOpenChange={(open) => !open && onClose()}>
      <DialogContent className="max-w-2xl gap-0 overflow-hidden border-border/60 bg-background p-0">
        <div className="border-b border-border/50 px-6 py-5">
          <DialogHeader>
            <DialogTitle className="text-lg font-semibold tracking-tight">
              Filter Records
            </DialogTitle>
            <DialogDescription className="text-xs uppercase tracking-widest text-muted-foreground">
              Refine your view by adding conditions
            </DialogDescription>
          </DialogHeader>
        </div>

        <div className="max-h-[60vh] space-y-3 overflow-y-auto p-6">
          {rows.map((row, idx) => {
            const col = currentColumn(row.field)
            const needsValue = row.op !== "is" && row.op !== "is not"
            return (
              <div key={idx} className="group flex items-start gap-2">
                <div className="grid flex-1 grid-cols-1 gap-2 md:grid-cols-12">
                  <div className="md:col-span-4">
                    <Select
                      value={row.field}
                      onValueChange={(value) => {
                        if (value != null) update(idx, "field", value)
                      }}
                    >
                      <SelectTrigger className="h-9 w-full">
                        <SelectValue placeholder="Field" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          {columns.map((c) => (
                            <SelectItem key={c.name} value={c.name}>
                              {c.name}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="md:col-span-3">
                    <Select
                      value={row.op}
                      onValueChange={(value) => {
                        if (value != null) update(idx, "op", value)
                      }}
                    >
                      <SelectTrigger className="h-9 w-full">
                        <SelectValue placeholder="Operator" />
                      </SelectTrigger>
                      <SelectContent>
                        <SelectGroup>
                          {OPERATORS.map((o) => (
                            <SelectItem key={o.value} value={o.value}>
                              {o.label}
                            </SelectItem>
                          ))}
                        </SelectGroup>
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="md:col-span-5">
                    {needsValue && col?.type === "ENUM" && col.options ? (
                      <Select
                        value={row.value == null || row.value === "" ? EMPTY_VALUE : String(row.value)}
                        onValueChange={(value) => update(idx, "value", value === EMPTY_VALUE ? "" : value)}
                      >
                        <SelectTrigger className="h-9 w-full">
                          <SelectValue placeholder="Value" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            <SelectItem value={EMPTY_VALUE}>—</SelectItem>
                            {col.options.map((opt) => (
                              <SelectItem key={opt} value={opt}>
                                {opt}
                              </SelectItem>
                            ))}
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                    ) : needsValue && col?.type === "BOOLEAN" ? (
                      <Select
                        value={row.value == null || row.value === "" ? EMPTY_VALUE : String(row.value)}
                        onValueChange={(value) => update(idx, "value", value === EMPTY_VALUE ? "" : value === "true")}
                      >
                        <SelectTrigger className="h-9 w-full">
                          <SelectValue placeholder="Value" />
                        </SelectTrigger>
                        <SelectContent>
                          <SelectGroup>
                            <SelectItem value={EMPTY_VALUE}>—</SelectItem>
                            <SelectItem value="true">Yes</SelectItem>
                            <SelectItem value="false">No</SelectItem>
                          </SelectGroup>
                        </SelectContent>
                      </Select>
                    ) : needsValue ? (
                      <Input
                        value={String(row.value ?? "")}
                        onChange={(e) => update(idx, "value", e.target.value)}
                        placeholder="Value"
                        className={inputClass}
                      />
                    ) : (
                      <div className={inputClass + " flex items-center text-muted-foreground"}>
                        —
                      </div>
                    )}
                  </div>
                </div>
                <button
                  type="button"
                  onClick={() => remove(idx)}
                  className="rounded-lg p-2 text-muted-foreground transition-colors hover:bg-destructive/10 hover:text-destructive"
                >
                  <Trash2 className="h-4 w-4" />
                </button>
              </div>
            )
          })}

          <Button
            variant="ghost"
            size="sm"
            onClick={add}
            className="w-full border border-dashed border-border text-muted-foreground hover:bg-muted/50 hover:text-foreground"
          >
            <Plus className="mr-2 h-4 w-4" />
            Add Condition
          </Button>
        </div>

        <div className="flex items-center justify-end gap-3 border-t border-border/50 bg-muted/30 px-6 py-4">
          <Button variant="ghost" onClick={onClose}>
            Cancel
          </Button>
          <Button onClick={apply}>Apply Filters</Button>
        </div>
      </DialogContent>
    </Dialog>
  )
}
