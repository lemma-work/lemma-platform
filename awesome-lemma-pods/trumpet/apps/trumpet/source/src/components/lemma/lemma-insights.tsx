import * as React from "react"
import { BarChart3, TrendingUp, TrendingDown, Minus } from "lucide-react"
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/lemma/ui/card"
import { useRecords, useFunctionRun } from "lemma-sdk/react"
import type { LemmaClient, RecordFilter } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import {
  BarChart,
  Bar,
  AreaChart,
  Area,
  LineChart,
  Line,
  PieChart,
  Pie,
  Cell,
  XAxis,
  YAxis,
  CartesianGrid,
  Tooltip,
  Legend,
  ResponsiveContainer,
} from "recharts"

const CHART_COLORS = [
  "var(--chart-1)",
  "var(--chart-2)",
  "var(--chart-3)",
  "var(--chart-4)",
  "var(--chart-5)",
]

export type StatSource =
  | { type: "count"; table: string; filters?: RecordFilter[]; label?: string }
  | { type: "sum"; table: string; field: string; filters?: RecordFilter[]; label?: string }
  | { type: "avg"; table: string; field: string; filters?: RecordFilter[]; label?: string }
  | { type: "function"; functionName: string; input?: Record<string, unknown>; extractPath?: string; label?: string }

export type ChartSource =
  | {
      type: "bar" | "line" | "area" | "funnel"
      table: string
      category: string
      value?: string
      aggregate?: "count" | "sum" | "avg"
      filters?: RecordFilter[]
      sortBy?: "category" | "value"
      order?: "asc" | "desc"
      limit?: number
    }
  | {
      type: "pie"
      table: string
      category: string
      value?: string
      aggregate?: "count" | "sum" | "avg"
      filters?: RecordFilter[]
      sortBy?: "category" | "value"
      order?: "asc" | "desc"
      limit?: number
    }
  | {
      type: "bar" | "line" | "area" | "pie" | "funnel"
      table?: undefined
      function: string
      input?: Record<string, unknown>
      extractPath?: string
    }

export interface StatCardDef {
  source: StatSource
  title: string
  format?: (value: number) => string
  trend?: "up" | "down" | "flat"
  trendLabel?: string
}

export interface ChartCardDef {
  source: ChartSource
  title: string
  description?: string
  height?: number
  valueFormatter?: (value: number) => string
  categoryFormatter?: (value: string) => string
  footer?: React.ReactNode
  emptyState?: React.ReactNode
}

export type LemmaInsightsRadius = "none" | "sm" | "md" | "lg" | "xl"

export type AggregationMode = "client" | "function"

export interface LemmaInsightsProps {
  client: LemmaClient
  podId?: string
  stats?: StatCardDef[]
  charts?: ChartCardDef[]
  columns?: 1 | 2 | 3 | 4
  aggregationMode?: AggregationMode
  aggregateFunctionName?: string
  appearance?: "default" | "minimal" | "borderless" | "contained"
  density?: "compact" | "comfortable" | "spacious"
  radius?: LemmaInsightsRadius
  className?: string
}

export function LemmaInsights({
  client,
  podId,
  stats = [],
  charts = [],
  columns = 4,
  aggregationMode = "client",
  aggregateFunctionName,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  className,
}: LemmaInsightsProps) {
  const gridCols = { 1: "grid-cols-1", 2: "grid-cols-2", 3: "grid-cols-3", 4: "grid-cols-4" }[columns]
  const gapClassName = density === "compact" ? "gap-2" : density === "spacious" ? "gap-5" : "gap-4"

  return (
    <div data-appearance={appearance} data-density={density} data-radius={radius} className={cn("flex flex-col", density === "compact" ? "gap-4" : density === "spacious" ? "gap-7" : "gap-6", className)}>
      {stats.length > 0 && (
        <div className={cn("grid", gridCols, gapClassName)}>
          {stats.map((def, i) => (
            <StatCard key={i} def={def} client={client} podId={podId} aggregationMode={aggregationMode} aggregateFunctionName={aggregateFunctionName} appearance={appearance} density={density} radius={radius} />
          ))}
        </div>
      )}

      {charts.length > 0 && (
        <div className={cn("grid grid-cols-1 lg:grid-cols-2", gapClassName)}>
          {charts.map((def, i) => (
            <ChartCard key={i} def={def} client={client} podId={podId} aggregationMode={aggregationMode} aggregateFunctionName={aggregateFunctionName} appearance={appearance} density={density} radius={radius} />
          ))}
        </div>
      )}
    </div>
  )
}

function StatCard({
  def,
  client,
  podId,
  aggregationMode,
  aggregateFunctionName,
  appearance,
  density,
  radius,
}: {
  def: StatCardDef
  client: LemmaClient
  podId?: string
  aggregationMode: AggregationMode
  aggregateFunctionName?: string
  appearance: NonNullable<LemmaInsightsProps["appearance"]>
  density: NonNullable<LemmaInsightsProps["density"]>
  radius: LemmaInsightsRadius
}) {
  const { source, title, format, trend, trendLabel } = def

  const isClientAgg = aggregationMode === "client"
  const isFnAgg = aggregationMode === "function" && !!aggregateFunctionName

  const recordsState = useRecords({
    client,
    podId,
    tableName: ("table" in source ? source.table : "") as string,
    filters: "filters" in source ? source.filters : undefined,
    enabled: source.type !== "function" && isClientAgg,
    limit: source.type === "count" ? 1 : source.type === "sum" || source.type === "avg" ? 500 : 1000,
  })

  const fnRun = useFunctionRun({
    client,
    podId,
    functionName: source.type === "function" ? source.functionName : isFnAgg ? aggregateFunctionName : undefined,
  })

  const [fnResult, setFnResult] = React.useState<number | null>(null)
  const [fnLoading, setFnLoading] = React.useState(source.type === "function" || isFnAgg)
  React.useEffect(() => {
    if (source.type === "function") {
      fnRun.start(source.input ?? {}).then((res) => {
        if (source.extractPath) {
          const val = extractNested(res.output_data, source.extractPath)
          setFnResult(typeof val === "number" ? val : 0)
        } else {
          setFnResult(typeof res.output_data === "number" ? res.output_data : 0)
        }
      }).catch(() => {
        setFnResult(0)
      }).finally(() => {
        setFnLoading(false)
      })
    } else if (isFnAgg && aggregateFunctionName) {
      const input: Record<string, unknown> = { aggregation_type: source.type }
      if ("table" in source) input.table = source.table
      if ("field" in source) input.field = source.field
      if ("filters" in source && source.filters) input.filters = source.filters
      fnRun.start(input).then((res) => {
        const val = res.output_data
        setFnResult(typeof val === "number" ? val : typeof val === "object" && val !== null ? Number((val as Record<string, unknown>).result) || 0 : 0)
      }).catch(() => {
        setFnResult(0)
      }).finally(() => {
        setFnLoading(false)
      })
    }
  }, [source.type, isFnAgg])

  let value = 0

  if (isFnAgg && fnResult != null) {
    value = fnResult
  } else if (source.type === "count") {
    value = recordsState.total
  } else if (source.type === "sum" || source.type === "avg") {
    const records = recordsState.records
    const nums = records.map((r) => Number(r[source.field])).filter((n) => !Number.isNaN(n))
    if (source.type === "sum") value = nums.reduce((a, b) => a + b, 0)
    else value = nums.length ? nums.reduce((a, b) => a + b, 0) / nums.length : 0
  } else if (fnResult != null) {
    value = fnResult
  }

  const displayValue = format ? format(value) : defaultFormat(value, source.type)
  const isLoading = isFnAgg ? fnLoading : source.type !== "function" ? recordsState.isLoading : fnLoading

  const TrendIcon = trend === "up" ? TrendingUp : trend === "down" ? TrendingDown : Minus
  const trendColor =
    trend === "up"
      ? "text-primary"
      : trend === "down"
        ? "text-destructive"
        : "text-muted-foreground"

  return (
    <Card className={insightCardClassName(appearance, radius)}>
      <CardHeader className={cn("flex flex-row items-center justify-between", density === "compact" ? "px-3 pb-1 pt-3" : density === "spacious" ? "px-5 pb-3 pt-5" : "px-4 pb-2 pt-4")}>
        <CardTitle className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">
          {title}
        </CardTitle>
        {trend && (
          <TrendIcon className={cn("size-4", trendColor)} />
        )}
      </CardHeader>
      <CardContent className={density === "compact" ? "px-3 pb-3" : density === "spacious" ? "px-5 pb-5" : "px-4 pb-4"}>
        <div className={cn("font-bold tracking-tight text-foreground", density === "compact" ? "text-xl" : "text-2xl")}>
          {isLoading ? "—" : displayValue}
        </div>
        {trendLabel && (
          <p className={`mt-0.5 text-xs ${trendColor}`}>{trendLabel}</p>
        )}
      </CardContent>
    </Card>
  )
}

function ChartCard({
  def,
  client,
  podId,
  aggregationMode,
  aggregateFunctionName,
  appearance,
  density,
  radius,
}: {
  def: ChartCardDef
  client: LemmaClient
  podId?: string
  aggregationMode: AggregationMode
  aggregateFunctionName?: string
  appearance: NonNullable<LemmaInsightsProps["appearance"]>
  density: NonNullable<LemmaInsightsProps["density"]>
  radius: LemmaInsightsRadius
}) {
  const { source, title, description, height = 300, valueFormatter, categoryFormatter, footer, emptyState } = def

  const isTableSource = "table" in source && source.table

  const isFnAgg = aggregationMode === "function" && !!aggregateFunctionName

  const recordsState = useRecords({
    client,
    podId,
    tableName: isTableSource ? (source.table as string) : "",
    filters: "filters" in source ? source.filters : undefined,
    enabled: !!isTableSource && !isFnAgg,
    limit: 200,
  })

  const [fnData, setFnData] = React.useState<Array<Record<string, unknown>>>([])
  const [fnLoading, setFnLoading] = React.useState(false)

  const shouldUseFnAgg = isFnAgg && isTableSource
  const shouldUseFnSource = !isTableSource

  React.useEffect(() => {
    if (shouldUseFnAgg && aggregateFunctionName) {
      setFnLoading(true)
      const input: Record<string, unknown> = { chart_type: source.type }
      if ("table" in source) input.table = source.table
      if ("category" in source) input.category = source.category
      if ("value" in source && source.value) input.value = source.value
      if ("filters" in source && source.filters) input.filters = source.filters
      client.withPod(podId ?? (client as { podId?: string }).podId ?? "")
        .functions.runs.create(aggregateFunctionName, { input })
        .then((run) => {
          const output = run.output_data
          const extractPath = "extractPath" in source && typeof source.extractPath === "string" ? source.extractPath : ""
          const extracted = extractPath ? extractNested(output, extractPath) : output
          if (Array.isArray(extracted)) setFnData(extracted as Array<Record<string, unknown>>)
          else if (typeof extracted === "object" && extracted !== null) setFnData([extracted as Record<string, unknown>])
          else setFnData([])
        })
        .catch(() => setFnData([]))
        .finally(() => setFnLoading(false))
      return
    }
    if (shouldUseFnSource) {
      setFnLoading(true)
      const fnSource = source as unknown as { function: string; input?: Record<string, unknown> }
      client.withPod(podId ?? (client as { podId?: string }).podId ?? "")
        .functions.runs.create(fnSource.function, {
          input: fnSource.input ?? {},
        })
        .then((run) => {
          const output = run.output_data
          const extractPath = "extractPath" in source && typeof source.extractPath === "string" ? source.extractPath : ""
          const extracted = extractPath ? extractNested(output, extractPath) : output
          if (Array.isArray(extracted)) setFnData(extracted as Array<Record<string, unknown>>)
          else if (typeof extracted === "object" && extracted !== null) setFnData([extracted as Record<string, unknown>])
          else setFnData([])
        })
        .catch(() => setFnData([]))
        .finally(() => setFnLoading(false))
      return
    }
  }, [shouldUseFnAgg, shouldUseFnSource])

  const data = shouldUseFnAgg || shouldUseFnSource ? normalizeFunctionChartData(fnData, source) : aggregateChartData(recordsState.records, source)
  const isLoading = shouldUseFnAgg || shouldUseFnSource ? fnLoading : recordsState.isLoading
  const chartHeight = density === "compact" ? Math.max(180, height - 60) : density === "spacious" ? height + 40 : height
  const hasData = data.length > 0
  const formatValue = valueFormatter ?? ((value: number) => value.toLocaleString(undefined, { maximumFractionDigits: 2 }))
  const formatCategory = categoryFormatter ?? ((value: string) => value)

  return (
    <Card className={insightCardClassName(appearance, radius)}>
      <CardHeader className={density === "compact" ? "px-3 pb-1 pt-3" : density === "spacious" ? "px-5 pb-3 pt-5" : "px-4 pb-2 pt-4"}>
        <CardTitle className="text-sm font-semibold tracking-tight text-foreground">
          {title}
        </CardTitle>
        {description ? (
          <CardDescription>{description}</CardDescription>
        ) : null}
      </CardHeader>
      <CardContent className={density === "compact" ? "px-3 pb-3" : density === "spacious" ? "px-5 pb-5" : "px-4 pb-4"}>
        {isLoading ? (
          <div className="flex items-center justify-center" style={{ height: chartHeight }}>
            <div className="size-6 animate-spin rounded-full border-2 border-muted-foreground/30 border-t-muted-foreground" />
          </div>
        ) : null}
        {!isLoading && !hasData ? (
          <div className="flex flex-col items-center justify-center gap-3 text-center text-muted-foreground" style={{ height: chartHeight }}>
            <span className={cn("flex size-10 items-center justify-center border border-border/60 bg-muted/35", insightRadiusClassName(radius))}>
              <BarChart3 className="size-4" />
            </span>
            <p className="max-w-sm text-sm">{emptyState ?? "No chart data is available for this source."}</p>
          </div>
        ) : null}
        {!isLoading && hasData ? (
        <>
        {source.type === "funnel" ? (
          <FunnelChartBlock data={data} height={chartHeight} formatValue={formatValue} formatCategory={formatCategory} density={density} />
        ) : (
        <ResponsiveContainer width="100%" height={chartHeight}>
          {source.type === "bar" ? (
            <BarChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="category" tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={formatCategory} />
              <YAxis tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={(value) => formatValue(Number(value))} />
              <Tooltip
                formatter={(value) => formatValue(Number(value))}
                labelFormatter={(label) => formatCategory(String(label))}
                contentStyle={{
                  backgroundColor: "var(--card)",
                  border: "1px solid var(--border)",
                  borderRadius: "8px",
                  fontSize: "12px",
                }}
              />
              <Bar dataKey="value" fill="var(--chart-1)" radius={[6, 6, 2, 2]} barSize={28} />
            </BarChart>
          ) : source.type === "area" ? (
            <AreaChart data={data}>
              <defs>
                <linearGradient id="lemma-insights-area" x1="0" y1="0" x2="0" y2="1">
                  <stop offset="5%" stopColor="var(--chart-1)" stopOpacity={0.35} />
                  <stop offset="95%" stopColor="var(--chart-1)" stopOpacity={0.02} />
                </linearGradient>
              </defs>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="category" tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={formatCategory} />
              <YAxis tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={(value) => formatValue(Number(value))} />
              <Tooltip
                formatter={(value) => formatValue(Number(value))}
                labelFormatter={(label) => formatCategory(String(label))}
                contentStyle={{
                  backgroundColor: "var(--card)",
                  border: "1px solid var(--border)",
                  borderRadius: "8px",
                  fontSize: "12px",
                }}
              />
              <Area type="monotone" dataKey="value" stroke="var(--chart-1)" strokeWidth={2} fill="url(#lemma-insights-area)" />
            </AreaChart>
          ) : source.type === "line" ? (
            <LineChart data={data}>
              <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
              <XAxis dataKey="category" tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={formatCategory} />
              <YAxis tick={{ fontSize: 11 }} stroke="var(--muted-foreground)" tickFormatter={(value) => formatValue(Number(value))} />
              <Tooltip
                formatter={(value) => formatValue(Number(value))}
                labelFormatter={(label) => formatCategory(String(label))}
                contentStyle={{
                  backgroundColor: "var(--card)",
                  border: "1px solid var(--border)",
                  borderRadius: "8px",
                  fontSize: "12px",
                }}
              />
              <Line type="monotone" dataKey="value" stroke="var(--chart-1)" strokeWidth={2} dot={false} />
            </LineChart>
          ) : (
            <PieChart>
              <Pie
                data={data}
                dataKey="value"
                nameKey="category"
                cx="50%"
                cy="50%"
                outerRadius={height / 3}
                label={({ payload, percent }) => {
                  const category =
                    payload && typeof payload === "object" && "category" in payload
                      ? String(payload.category ?? "")
                      : "";
                  const ratio = typeof percent === "number" ? percent : 0;
                  return `${formatCategory(category)} ${(ratio * 100).toFixed(0)}%`;
                }}
                labelLine={false}
              >
                {data.map((_, i) => (
                  <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
                ))}
              </Pie>
              <Tooltip
                formatter={(value) => formatValue(Number(value))}
                contentStyle={{
                  backgroundColor: "var(--card)",
                  border: "1px solid var(--border)",
                  borderRadius: "8px",
                  fontSize: "12px",
                }}
              />
              <Legend formatter={(value) => formatCategory(String(value))} />
            </PieChart>
          )}
        </ResponsiveContainer>
        )}
        {footer ? <div className="mt-3 text-xs text-muted-foreground">{footer}</div> : null}
        </>
        ) : null}
      </CardContent>
    </Card>
  )
}

function FunnelChartBlock({
  data,
  height,
  formatValue,
  formatCategory,
  density,
}: {
  data: Array<{ category: string; value: number }>
  height: number
  formatValue: (value: number) => string
  formatCategory: (value: string) => string
  density: NonNullable<LemmaInsightsProps["density"]>
}) {
  const sorted = [...data].sort((a, b) => b.value - a.value)
  const maxValue = Math.max(...sorted.map((d) => d.value), 1)
  const stageHeight = density === "compact" ? 36 : density === "spacious" ? 52 : 44
  const connectorHeight = density === "compact" ? 8 : density === "spacious" ? 16 : 12
  const fontSize = density === "compact" ? "text-xs" : density === "spacious" ? "text-sm" : "text-xs"

  return (
    <div className="flex flex-col items-center justify-center overflow-hidden" style={{ height }}>
      {sorted.map((item, i) => {
        const widthPct = Math.max((item.value / maxValue) * 100, 10)
        const conversionRate = i > 0 ? ((item.value / sorted[i - 1].value) * 100).toFixed(1) : null
        const nextWidth = i < sorted.length - 1 ? Math.max((sorted[i + 1].value / maxValue) * 100, 10) : null
        return (
          <React.Fragment key={i}>
            {conversionRate !== null && (
              <div className={cn("flex items-center justify-center", density === "compact" ? "h-4" : "h-5")}>
                <span className="text-[10px] font-medium text-muted-foreground">{conversionRate}%</span>
              </div>
            )}
            <div className="flex justify-center w-full">
              <div
                className={cn("flex items-center justify-center rounded-sm font-medium text-white", fontSize)}
                style={{
                  width: `${widthPct}%`,
                  height: stageHeight,
                  backgroundColor: CHART_COLORS[i % CHART_COLORS.length],
                }}
              >
                <span className="truncate px-3">
                  {formatCategory(item.category)}: {formatValue(item.value)}
                </span>
              </div>
            </div>
            {nextWidth !== null && (
              <div className="flex justify-center w-full" style={{ height: connectorHeight }}>
                <svg
                  width="100%"
                  height={connectorHeight}
                  viewBox={`0 0 100 ${connectorHeight}`}
                  preserveAspectRatio="none"
                  style={{ overflow: "visible" }}
                >
                  <polygon
                    points={`${(100 - widthPct) / 2},0 ${100 - (100 - widthPct) / 2},0 ${100 - (100 - nextWidth) / 2},${connectorHeight} ${(100 - nextWidth) / 2},${connectorHeight}`}
                    fill={CHART_COLORS[i % CHART_COLORS.length]}
                    opacity={0.7}
                  />
                </svg>
              </div>
            )}
          </React.Fragment>
        )
      })}
    </div>
  )
}

function insightCardClassName(appearance: NonNullable<LemmaInsightsProps["appearance"]>, radius: LemmaInsightsRadius) {
  const radiusClassName = insightRadiusClassName(radius)
  if (appearance === "borderless") return cn(radiusClassName, "border-0 shadow-none ring-0")
  if (appearance === "minimal") return cn(radiusClassName, "border-0 bg-transparent shadow-none ring-0")
  if (appearance === "contained") return cn(radiusClassName, "border-border/70 shadow-sm")
  return cn(radiusClassName, "border-border/50")
}

function insightRadiusClassName(radius: LemmaInsightsRadius = "lg") {
  if (radius === "none") return "rounded-none"
  if (radius === "sm") return "rounded-md"
  if (radius === "md") return "rounded-lg"
  if (radius === "xl") return "rounded-2xl"
  return "rounded-xl"
}

function aggregateChartData(
  records: Record<string, unknown>[],
  source: ChartSource,
): Array<{ category: string; value: number }> {
  if (!("category" in source)) return []

  const catField = source.category
  const valField = source.value
  const aggregate = source.aggregate ?? (valField ? "sum" : "count")
  const groups = new Map<string, { count: number; sum: number }>()
  for (const rec of records) {
    const cat = String(rec[catField] ?? "Unknown")
    const current = groups.get(cat) ?? { count: 0, sum: 0 }
    const rawValue = valField ? Number(rec[valField] ?? 0) : 1
    current.count += 1
    current.sum += Number.isNaN(rawValue) ? 0 : rawValue
    groups.set(cat, current)
  }

  const data = Array.from(groups.entries()).map(([category, value]) => ({
    category,
    value: aggregate === "avg" ? (value.count ? value.sum / value.count : 0) : aggregate === "count" ? value.count : value.sum,
  }))

  return sortAndLimitChartData(data, source)
}

function normalizeFunctionChartData(
  rows: Array<Record<string, unknown>>,
  source: ChartSource,
): Array<{ category: string; value: number }> {
  const data = rows
    .map((row) => ({
      category: String(row.category ?? row.name ?? row.label ?? row.key ?? "Unknown"),
      value: Number(row.value ?? row.count ?? row.total ?? 0),
    }))
    .filter((row) => !Number.isNaN(row.value))

  return sortAndLimitChartData(data, source)
}

function sortAndLimitChartData(
  data: Array<{ category: string; value: number }>,
  source: ChartSource,
) {
  const sortBy = "sortBy" in source ? source.sortBy : undefined
  const order = "order" in source ? source.order ?? "desc" : "desc"
  const limit = "limit" in source ? source.limit : undefined
  const sorted = [...data].sort((a, b) => {
    const comparison = sortBy === "category" ? a.category.localeCompare(b.category) : a.value - b.value
    return order === "asc" ? comparison : -comparison
  })
  return typeof limit === "number" && limit > 0 ? sorted.slice(0, limit) : sorted
}

function defaultFormat(value: number, type: string): string {
  if (type === "count") return value.toLocaleString()
  if (type === "avg") return value.toFixed(1)
  if (type === "sum") return value.toLocaleString(undefined, { maximumFractionDigits: 2 })
  return String(value)
}

function extractNested(obj: unknown, path: string): unknown {
  const keys = path.split(".")
  let current = obj
  for (const key of keys) {
    if (current == null || typeof current !== "object") return undefined
    current = (current as Record<string, unknown>)[key]
  }
  return current
}
