import * as React from "react"
import {
  AlertCircle,
  ArrowDown,
  ArrowUp,
  Calendar,
  ChevronLeft,
  ChevronRight,
  Database,
  Filter,
  List,
  LayoutGrid,
  Plus,
  RefreshCw,
  Search,
  Trash2,
  MoreVertical,
  Rows3,
  X,
} from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import { Input } from "@/components/lemma/ui/input"
import { Checkbox } from "@/components/lemma/ui/checkbox"
import { Skeleton } from "@/components/lemma/ui/skeleton"
import {
  Table as DataTable,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/lemma/ui/table"
import {
  useTable,
  useRecords,
  useForeignKeyOptions,
} from "lemma-sdk/react"
import type { LemmaClient, ColumnSchema, RecordFilter, Table } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import { EditableCell } from "./records-editable-cell"
import { FilterBuilder } from "./records-filter-builder"
import { DetailSheet } from "./records-detail-sheet"
import { ListView } from "./records-list-view"
import { GroupedView } from "./records-grouped-view"
import { RecordsCalendarView, RecordsMatrixView, RecordsTimelineView } from "./records-consolidated-views"
import { isSystemField, typeBadgeClasses, enumPillClasses, type EnumColorMap } from "./records-enum-utils"
import { RecordFormSheet } from "./records-form-sheet"
import { RecordDetail } from "./records-detail"
import type { ForeignKeyLabelMap } from "./records-display-utils"
import type {
  RecordDetailFieldGroup,
  RecordDetailRelatedRecord,
  RecordDetailSectionVisibilityRule,
  RecordDetailTabContext,
  RecordDetailTab,
  RecordDetailVariant,
} from "./records-detail"
import type { RecordPreviewDisplayOptions } from "./records-display-utils"
import {
  createRecordActionInput,
  RecordQuickActionButtons,
  recordActionKey,
  recordQuickActionKey,
  resolveRecordActionMode,
  resolveRecordActionValues,
  type RecordQuickAction,
  type RecordQuickActionContext,
  type RecordQuickActionMode,
  type RecordQuickActionPlacement,
} from "./records-quick-actions"
import {
  recordsAppearanceFromSurface,
  recordsRadiusClassName,
  type LemmaRecordsAppearance,
  type LemmaRecordsDensity,
  type LemmaRecordsRadius,
  type LemmaRecordsSurface,
} from "./records-style-utils"

export type { LemmaRecordsAppearance, LemmaRecordsDensity, LemmaRecordsRadius, LemmaRecordsSurface } from "./records-style-utils"

type ViewMode = "grid" | "list" | "grouped" | "kanban" | "linear" | "calendar" | "timeline" | "matrix"
type ResolvedViewMode = "grid" | "list" | "kanban" | "linear" | "calendar" | "timeline" | "matrix"
type CreateMode = "sheet" | "modal" | "page"
type DetailMode = "sheet" | "modal" | "page" | "inline"
type PaginationMode = "pagination" | "load-more" | "infinite"
export type LemmaRecordsViewPreset = "default" | "triage" | "issues" | "crm" | "docs"
type LegacyDetailTabId = "comments" | "activity" | "files"
type LegacyCompatibleRecordDetailTab = RecordDetailTab | LegacyDetailTabId

export interface LemmaRecordsViewChrome {
  title?: boolean
  search?: boolean
  filters?: boolean
  create?: boolean
  viewSwitcher?: boolean
  selection?: boolean
  footer?: boolean
}

interface RecordsViewPresetConfig {
  defaultView?: ViewMode
  availableViews?: ViewMode[]
  groupByFields?: string[]
  visibleColumns?: string[]
  hiddenFields?: string[]
  searchFields?: string[]
  searchPlaceholder?: string
  pageSize?: number
  paginationMode?: PaginationMode
  createMode?: CreateMode
  detailMode?: DetailMode
  detailVariant?: RecordDetailVariant
  detailTabs?: LegacyCompatibleRecordDetailTab[]
  quickActionPlacement?: RecordQuickActionPlacement
  listOptions?: RecordPreviewDisplayOptions
  groupedOptions?: RecordPreviewDisplayOptions
  chrome?: LemmaRecordsViewChrome
}

const RECORDS_VIEW_PRESETS: Record<LemmaRecordsViewPreset, RecordsViewPresetConfig> = {
  default: {},
  triage: {
    defaultView: "linear",
    availableViews: ["linear", "kanban", "calendar", "timeline", "list", "grid"],
    groupByFields: ["status", "state", "stage", "queue", "priority"],
    visibleColumns: ["identifier", "title", "subject", "summary", "status", "priority", "assignee_user_id", "updated_at"],
    searchFields: ["identifier", "title", "subject", "summary", "status", "priority"],
    searchPlaceholder: "Search queue...",
    detailMode: "inline",
    detailVariant: "workspace",
    quickActionPlacement: "both",
    pageSize: 75,
    paginationMode: "load-more",
    listOptions: { secondaryFields: ["status", "priority", "assignee_user_id", "updated_at"] },
    groupedOptions: { secondaryFields: ["priority", "assignee_user_id", "updated_at"] },
    chrome: { search: true, filters: true, create: true, viewSwitcher: true, selection: true },
  },
  issues: {
    defaultView: "linear",
    availableViews: ["linear", "kanban", "calendar", "timeline", "matrix", "list", "grid"],
    groupByFields: ["status", "state", "workflow_state", "stage"],
    visibleColumns: ["identifier", "title", "status", "priority", "team_id", "assignee_user_id", "updated_at"],
    searchFields: ["identifier", "title", "description", "status", "priority"],
    searchPlaceholder: "Search issues...",
    detailMode: "inline",
    detailVariant: "workspace",
    quickActionPlacement: "both",
    pageSize: 75,
    paginationMode: "load-more",
    listOptions: { secondaryFields: ["identifier", "status", "priority", "assignee_user_id"] },
    groupedOptions: { secondaryFields: ["identifier", "priority", "assignee_user_id"] },
    chrome: { search: true, filters: true, create: true, viewSwitcher: true, selection: true },
  },
  crm: {
    defaultView: "kanban",
    availableViews: ["kanban", "linear", "timeline", "matrix", "list", "grid"],
    groupByFields: ["stage", "status", "pipeline_stage", "deal_stage"],
    visibleColumns: ["name", "title", "company_id", "contact_id", "stage", "status", "amount", "owner_user_id", "next_step", "updated_at"],
    searchFields: ["name", "title", "company", "domain", "stage", "status", "source"],
    searchPlaceholder: "Search pipeline...",
    detailMode: "inline",
    detailVariant: "workspace",
    quickActionPlacement: "detail",
    pageSize: 100,
    paginationMode: "load-more",
    listOptions: { secondaryFields: ["company_id", "stage", "amount", "owner_user_id"] },
    groupedOptions: { secondaryFields: ["company_id", "amount", "owner_user_id"] },
    chrome: { search: true, filters: true, create: true, viewSwitcher: true, selection: true },
  },
  docs: {
    defaultView: "list",
    availableViews: ["list", "grid", "timeline", "kanban"],
    groupByFields: ["status", "type", "category", "space", "folder"],
    visibleColumns: ["title", "name", "status", "type", "owner_user_id", "updated_at"],
    searchFields: ["title", "name", "content", "summary", "slug", "path"],
    searchPlaceholder: "Search docs...",
    detailMode: "inline",
    detailVariant: "workspace",
    quickActionPlacement: "detail",
    pageSize: 100,
    paginationMode: "load-more",
    listOptions: { secondaryFields: ["status", "type", "owner_user_id", "updated_at"] },
    groupedOptions: { secondaryFields: ["type", "owner_user_id", "updated_at"] },
    chrome: { search: true, filters: true, create: true, viewSwitcher: true, selection: true },
  },
}

export interface LemmaRecordsViewProps {
  client: LemmaClient
  podId?: string
  tableName: string
  enabled?: boolean
  preset?: LemmaRecordsViewPreset

  visibleColumns?: string[]
  hiddenFields?: string[]
  pinnedColumns?: string[]
  primaryField?: string
  defaultVisibleColumnCount?: number
  showSystemFields?: boolean
  columnWidths?: Record<string, string | number>
  columnLabels?: Record<string, string>
  showTypeHints?: boolean
  enumColorMap?: EnumColorMap
  renderCell?: (record: Record<string, unknown>, column: ColumnSchema, value: unknown) => React.ReactNode
  renderCard?: (record: Record<string, unknown>, columns: ColumnSchema[]) => React.ReactNode
  foreignKeyLabels?: Record<string, string>
  searchFields?: string[]
  searchPlaceholder?: string

  defaultView?: ViewMode
  availableViews?: ViewMode[]
  chrome?: LemmaRecordsViewChrome
  surface?: LemmaRecordsSurface
  appearance?: LemmaRecordsAppearance
  density?: LemmaRecordsDensity
  radius?: LemmaRecordsRadius
  groupBy?: string
  defaultFilters?: RecordFilter[]
  defaultSort?: { field: string; order?: "asc" | "desc" }
  pageSize?: number
  paginationMode?: PaginationMode
  createMode?: CreateMode
  createRoute?: string | (() => string)
  detailMode?: DetailMode
  detailRoute?: (record: Record<string, unknown>) => string
  detailVariant?: RecordDetailVariant
  detailTabs?: LegacyCompatibleRecordDetailTab[]
  detailHeaderFields?: string[]
  detailFieldGroups?: RecordDetailFieldGroup[]
  detailRelatedRecords?: RecordDetailRelatedRecord[]
  detailTableLabel?: React.ReactNode
  detailTitleField?: string
  detailDescriptionField?: string
  detailIdentifierField?: string
  detailStatusField?: string
  detailEditable?: boolean
  detailActions?: React.ReactNode | ((context: { record: Record<string, unknown>; table: Table; recordId: string }) => React.ReactNode)
  quickActions?: RecordQuickAction[]
  bulkActions?: RecordQuickAction[]
  quickActionMode?: RecordQuickActionMode
  quickActionPlacement?: RecordQuickActionPlacement
  onQuickActionSuccess?: (context: RecordQuickActionContext) => void
  renderFilesTab?: (context: { record: Record<string, unknown>; table: Table; recordId: string }) => React.ReactNode
  renderCommentsTab?: (context: { record: Record<string, unknown>; table: Table; recordId: string }) => React.ReactNode
  renderActivityTab?: (context: { record: Record<string, unknown>; table: Table; recordId: string }) => React.ReactNode
  detailSectionLabels?: Partial<Record<string, React.ReactNode>>
  detailSectionVisibility?: Partial<Record<string, RecordDetailSectionVisibilityRule>>
  listOptions?: RecordPreviewDisplayOptions
  groupedOptions?: RecordPreviewDisplayOptions
  calendarField?: string
  timelineField?: string
  matrixRowsBy?: string
  matrixColumnsBy?: string

  onCreateOptions?: {
    submitVia?: "direct" | "function"
    submitFunctionName?: string
    hiddenFields?: string[]
    fieldOrder?: string[]
    fieldGroups?: Array<{ label: string; fields: string[] }>
    fieldVisibility?: Record<string, boolean | ((context: { values: Record<string, unknown>; fieldName: string }) => boolean)>
    sectionVisibility?: Record<string, boolean | ((context: { values: Record<string, unknown>; label: string; fields: string[] }) => boolean)>
  }
  onUpdateOptions?: {
    updateVia?: "direct" | "function"
    updateFunctionName?: string
  }

  title?: React.ReactNode
  headerActions?: React.ReactNode
  emptyState?: React.ReactNode
  className?: string
  onRecordClick?: (record: Record<string, unknown>) => void
}

export function LemmaRecordsView({
  client,
  podId,
  tableName,
  enabled = true,
  preset = "default",
  visibleColumns: visibleColumnNamesProp,
  hiddenFields: hiddenFieldsProp,
  pinnedColumns,
  primaryField,
  defaultVisibleColumnCount = 8,
  showSystemFields = false,
  columnWidths,
  columnLabels,
  showTypeHints = false,
  enumColorMap,
  renderCell,
  renderCard,
  foreignKeyLabels,
  searchFields: searchFieldsProp,
  searchPlaceholder: searchPlaceholderProp,
  defaultView: defaultViewProp,
  availableViews: availableViewsProp,
  chrome: chromeProp,
  surface,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  groupBy: groupByProp,
  defaultFilters = [],
  defaultSort,
  pageSize: pageSizeProp,
  paginationMode: paginationModeProp,
  createMode: createModeProp,
  createRoute,
  detailMode: detailModeProp,
  detailRoute,
  detailVariant: detailVariantProp,
  detailTabs: detailTabsProp,
  detailHeaderFields,
  detailFieldGroups,
  detailRelatedRecords,
  detailTableLabel,
  detailTitleField,
  detailDescriptionField,
  detailIdentifierField,
  detailStatusField,
  detailEditable = true,
  detailActions,
  quickActions,
  bulkActions,
  quickActionMode,
  quickActionPlacement: quickActionPlacementProp,
  onQuickActionSuccess,
  renderFilesTab,
  renderCommentsTab,
  renderActivityTab,
  detailSectionLabels,
  detailSectionVisibility,
  listOptions: listOptionsProp,
  groupedOptions: groupedOptionsProp,
  calendarField,
  timelineField,
  matrixRowsBy,
  matrixColumnsBy,
  onCreateOptions,
  onUpdateOptions,
  title,
  headerActions,
  emptyState,
  className,
  onRecordClick,
}: LemmaRecordsViewProps) {
  const presetConfig = RECORDS_VIEW_PRESETS[preset] ?? RECORDS_VIEW_PRESETS.default
  const defaultView = defaultViewProp ?? presetConfig.defaultView ?? "grid"
  const availableViews = availableViewsProp ?? presetConfig.availableViews
  const searchPlaceholder = searchPlaceholderProp ?? presetConfig.searchPlaceholder ?? "Search…"
  const pageSize = pageSizeProp ?? presetConfig.pageSize ?? 50
  const paginationMode = paginationModeProp ?? presetConfig.paginationMode ?? "pagination"
  const createMode = createModeProp ?? presetConfig.createMode ?? "sheet"
  const detailMode = detailModeProp ?? presetConfig.detailMode ?? "sheet"
  const detailVariant = detailVariantProp ?? presetConfig.detailVariant ?? "workspace"
  const quickActionPlacement = quickActionPlacementProp ?? presetConfig.quickActionPlacement ?? "detail"
  const listOptions = listOptionsProp ?? presetConfig.listOptions
  const groupedOptions = groupedOptionsProp ?? presetConfig.groupedOptions
  const searchEnabled = chromeProp?.search ?? presetConfig.chrome?.search ?? Boolean(searchFieldsProp?.length || searchPlaceholderProp)
  const filtersEnabled = chromeProp?.filters ?? presetConfig.chrome?.filters ?? defaultFilters.length > 0
  const createEnabled = chromeProp?.create ?? presetConfig.chrome?.create ?? Boolean(createRoute || createModeProp || onCreateOptions)
  const viewSwitcherEnabled = chromeProp?.viewSwitcher ?? presetConfig.chrome?.viewSwitcher ?? Boolean(availableViewsProp && availableViewsProp.length > 1)
  const selectionEnabled = chromeProp?.selection ?? presetConfig.chrome?.selection ?? Boolean(bulkActions?.length)
  const titleEnabled = chromeProp?.title ?? presetConfig.chrome?.title ?? true
  const footerEnabled = chromeProp?.footer ?? presetConfig.chrome?.footer ?? true
  const resolvedSurface = surface ?? (appearance === "borderless" ? "inherit" : appearance === "minimal" ? "muted" : "card")
  const resolvedAppearance = surface ? recordsAppearanceFromSurface(surface) : appearance
  const effectiveListOptions = React.useMemo(
    () => ({ ...(listOptions ?? {}), ...(primaryField ? { primaryField } : {}) }),
    [listOptions, primaryField],
  )
  const effectiveGroupedOptions = React.useMemo(
    () => ({ ...(groupedOptions ?? {}), ...(primaryField ? { primaryField } : {}) }),
    [groupedOptions, primaryField],
  )
  const hiddenFields = React.useMemo(
    () => uniqueStrings([...(presetConfig.hiddenFields ?? []), ...(hiddenFieldsProp ?? [])]),
    [hiddenFieldsProp, presetConfig.hiddenFields],
  )
  const detailTabs = React.useMemo(
    () =>
      resolveLegacyDetailTabs(detailTabsProp ?? presetConfig.detailTabs, {
        renderFiles: renderFilesTab,
        renderComments: renderCommentsTab,
        renderActivity: renderActivityTab,
      }),
    [detailTabsProp, presetConfig.detailTabs, renderActivityTab, renderCommentsTab, renderFilesTab],
  )
  const [viewMode, setViewMode] = React.useState<ResolvedViewMode>(() => normalizeViewMode(defaultView))
  const [filters, setFilters] = React.useState<RecordFilter[]>(defaultFilters)
  const [showFilterBuilder, setShowFilterBuilder] = React.useState(false)
  const [search, setSearch] = React.useState("")
  const [selectedRows, setSelectedRows] = React.useState<Set<string>>(new Set())
  const [detailRecordId, setDetailRecordId] = React.useState<string | null>(null)
  const [showCreateForm, setShowCreateForm] = React.useState(false)
  const [foreignKeyLabelMap, setForeignKeyLabelMap] = React.useState<ForeignKeyLabelMap>({})
  const [submittingQuickActionKey, setSubmittingQuickActionKey] = React.useState<string | null>(null)
  const [submittingBulkActionKey, setSubmittingBulkActionKey] = React.useState<string | null>(null)
  const [actionError, setActionError] = React.useState<string | null>(null)
  const [page, setPage] = React.useState(0)
  const [sortField, setSortField] = React.useState<string | null>(null)
  const [sortOrder, setSortOrder] = React.useState<"asc" | "desc">("asc")
  const contentRef = React.useRef<HTMLDivElement | null>(null)
  const [infiniteSentinel, setInfiniteSentinel] = React.useState<HTMLDivElement | null>(null)

  const tableState = useTable({ client, podId, tableName, enabled })
  const table = tableState.table
  const queryFilters = React.useMemo(() => normalizeRecordFilters(filters), [filters])
  const defaultFiltersKey = React.useMemo(
    () => JSON.stringify(normalizeRecordFilters(defaultFilters)),
    [defaultFilters],
  )

  const recordsState = useRecords({
    client,
    podId,
    tableName,
    filters: queryFilters.length > 0 ? queryFilters : undefined,
    sortBy: sortField ?? undefined,
    order: sortField ? sortOrder : undefined,
    limit: pageSize,
    offset: paginationMode === "pagination" ? page * pageSize : 0,
    enabled: !!table && enabled,
  })

  const records = recordsState.records
  const total = recordsState.total
  const pk = table?.primary_key_column ?? "id"
  const scopedClient = React.useMemo(
    () => (podId ? client.withPod(podId) : client),
    [client, podId],
  )

  const presetVisibleColumnNames = React.useMemo(
    () => pickExistingColumnNames(table, presetConfig.visibleColumns),
    [presetConfig.visibleColumns, table],
  )
  const visibleColumnNames = visibleColumnNamesProp ?? presetVisibleColumnNames
  const resolvedColumns = React.useMemo(() => {
    return resolveRecordColumns(table, {
      visibleColumnNames,
      hiddenFields,
      pinnedColumns,
      primaryField,
      defaultVisibleColumnCount,
      showSystemFields,
    })
  }, [defaultVisibleColumnCount, hiddenFields, pinnedColumns, primaryField, showSystemFields, table, visibleColumnNames])
  const resolvedDefaultSort = React.useMemo(
    () => resolveDefaultSort(table, defaultSort),
    [defaultSort, table],
  )
  const resolvedDefaultSortKey = React.useMemo(
    () => `${resolvedDefaultSort?.field ?? ""}:${resolvedDefaultSort?.order ?? "asc"}`,
    [resolvedDefaultSort],
  )

  const foreignKeyColumns = React.useMemo(
    () => resolvedColumns.filter((column) => !!column.foreign_key),
    [resolvedColumns],
  )

  const groupByColumn = React.useMemo(() => {
    if (!table) return null
    const presetGroupByField = pickFirstExistingColumnName(table, presetConfig.groupByFields)
    const groupByField = groupByProp ?? presetGroupByField
    if (groupByField) return table.columns.find((c) => c.name === groupByField) ?? null
    return null
  }, [table, groupByProp, presetConfig.groupByFields])
  const availableViewModes = React.useMemo(
    () => resolveAvailableViewModes(availableViews, defaultView, !!groupByColumn),
    [availableViews, defaultView, groupByColumn],
  )

  const getRecordId = (r: Record<string, unknown>) => String(r[pk] ?? "")
  const deferredSearch = React.useDeferredValue(search)
  const searchQuery = deferredSearch.trim().toLowerCase()
  const presetSearchFields = React.useMemo(
    () => pickExistingColumnNames(table, presetConfig.searchFields),
    [presetConfig.searchFields, table],
  )
  const searchFields = searchFieldsProp ?? presetSearchFields
  const searchableColumnNames = React.useMemo(() => {
    if (searchFields?.length) return searchFields
    return resolvedColumns.filter(isSearchableColumn).map((c) => c.name)
  }, [resolvedColumns, searchFields])
  const displayedRecords = React.useMemo(() => {
    if (!searchEnabled || !searchQuery) return records
    return records.filter((record) =>
      searchableColumnNames.some((name) => matchesSearchValue(record[name], searchQuery)),
    )
  }, [records, searchEnabled, searchQuery, searchableColumnNames])
  const displayedRecordIds = React.useMemo(() => displayedRecords.map(getRecordId), [displayedRecords, pk])
  const selectedRecordsList = React.useMemo(
    () => records.filter((record) => selectedRows.has(getRecordId(record))),
    [records, selectedRows, pk],
  )
  const detailRecord = React.useMemo(() => {
    if (!detailRecordId) return null
    return records.find((record) => getRecordId(record) === detailRecordId) ?? null
  }, [detailRecordId, records, pk])
  const allDisplayedSelected =
    displayedRecordIds.length > 0 && displayedRecordIds.every((id) => selectedRows.has(id))
  const hasSearch = searchEnabled && searchQuery.length > 0
  const hasFilters = filters.length > 0
  const hasActiveConstraints = hasSearch || hasFilters
  const pageStart = total === 0 ? 0 : page * pageSize + 1
  const pageEnd = Math.min(page * pageSize + records.length, total)
  const isGroupedView = (viewMode === "kanban" || viewMode === "linear") && !!groupByColumn
  const canLoadMore = records.length < total || !!recordsState.nextPageToken
  const progressivePagination = paginationMode === "load-more" || paginationMode === "infinite"
  const detailRecordIndex = detailRecord
    ? displayedRecords.findIndex((r) => getRecordId(r) === getRecordId(detailRecord))
    : -1
  const customDetailActionContent = React.useMemo(() => {
    if (!detailRecord || !table || !detailActions) return null
    const recordId = getRecordId(detailRecord)
    return typeof detailActions === "function"
      ? detailActions({ record: detailRecord, table, recordId })
      : detailActions
  }, [detailActions, detailRecord, table])
  const previewQuickActionsEnabled = quickActions != null && quickActions.length > 0 && (quickActionPlacement === "preview" || quickActionPlacement === "both")
  const detailQuickActionsEnabled = quickActions != null && quickActions.length > 0 && (quickActionPlacement === "detail" || quickActionPlacement === "both")

  React.useEffect(() => {
    if (availableViewModes.length === 0) return
    if (!availableViewModes.includes(viewMode)) {
      setViewMode(availableViewModes[0])
    }
  }, [availableViewModes, viewMode])

  React.useEffect(() => {
    if (!selectionEnabled && selectedRows.size > 0) {
      setSelectedRows(new Set())
    }
  }, [selectedRows.size, selectionEnabled])

  React.useEffect(() => {
    if (!filtersEnabled && showFilterBuilder) {
      setShowFilterBuilder(false)
    }
  }, [filtersEnabled, showFilterBuilder])

  React.useEffect(() => {
    setFilters(defaultFilters)
    setSearch("")
    setSelectedRows(new Set())
    setDetailRecordId(null)
    setPage(0)
    setSortField(resolvedDefaultSort?.field ?? null)
    setSortOrder(resolvedDefaultSort?.order ?? "asc")
    setViewMode(normalizeViewMode(defaultView))
  }, [defaultFiltersKey, defaultView, podId, resolvedDefaultSortKey, tableName])

  React.useEffect(() => {
    if (!detailRecordId) return
    if (records.some((record) => getRecordId(record) === detailRecordId)) return
    if (detailMode === "inline" && displayedRecords.length > 0) {
      setDetailRecordId(getRecordId(displayedRecords[0]))
      return
    }
    setDetailRecordId(null)
  }, [detailMode, detailRecordId, displayedRecords, records, pk])

  React.useEffect(() => {
    if (detailMode !== "inline") return
    if (displayedRecords.length === 0) {
      setDetailRecordId(null)
      return
    }
    if (!detailRecordId || !displayedRecords.some((record) => getRecordId(record) === detailRecordId)) {
      setDetailRecordId(getRecordId(displayedRecords[0]))
    }
  }, [detailMode, detailRecordId, displayedRecords, pk])

  const handleSortColumn = (colName: string) => {
    if (sortField === colName) {
      if (sortOrder === "asc") {
        setSortOrder("desc")
      } else {
        setSortField(null)
        setSortOrder("asc")
      }
    } else {
      setSortField(colName)
      setSortOrder("asc")
    }
    setPage(0)
  }

  const applyFilters = (nextFilters: RecordFilter[]) => {
    setFilters(nextFilters)
    setPage(0)
    setSelectedRows(new Set())
  }

  const clearSearch = () => setSearch("")

  const clearAllConstraints = () => {
    setSearch("")
    applyFilters([])
  }

  const handleSelectAll = () => {
    if (allDisplayedSelected) {
      setSelectedRows((prev) => {
        const next = new Set(prev)
        for (const id of displayedRecordIds) next.delete(id)
        return next
      })
    } else {
      setSelectedRows((prev) => {
        const next = new Set(prev)
        for (const id of displayedRecordIds) next.add(id)
        return next
      })
    }
  }

  const handleSelectRow = (id: string) => {
    setSelectedRows((prev) => {
      const next = new Set(prev)
      if (next.has(id)) next.delete(id)
      else next.add(id)
      return next
    })
  }

  const handleRecordClick = (record: Record<string, unknown>) => {
    if (onRecordClick) {
      onRecordClick(record)
      return
    }
    if (detailMode === "page" && detailRoute) {
      navigateTo(detailRoute(record))
      return
    }
    setDetailRecordId(getRecordId(record))
  }

  const handleLoadMore = React.useCallback(async () => {
    if (!canLoadMore || recordsState.isLoading || recordsState.isLoadingMore) return
    await recordsState.loadMore()
  }, [canLoadMore, recordsState])

  React.useEffect(() => {
    if (paginationMode !== "infinite") return
    if (!infiniteSentinel || !canLoadMore || recordsState.isLoading || recordsState.isLoadingMore) return

    const observer = new IntersectionObserver(
      (entries) => {
        if (entries.some((entry) => entry.isIntersecting)) {
          void handleLoadMore()
        }
      },
      { root: contentRef.current, rootMargin: "320px 0px" },
    )

    observer.observe(infiniteSentinel)
    return () => observer.disconnect()
  }, [
    canLoadMore,
    handleLoadMore,
    infiniteSentinel,
    paginationMode,
    recordsState.isLoading,
    recordsState.isLoadingMore,
  ])

  const handleCreateClick = () => {
    if (!createEnabled) return
    if (createMode === "page" && createRoute) {
      navigateTo(typeof createRoute === "function" ? createRoute() : createRoute)
      return
    }
    setShowCreateForm(true)
  }

  const handleDeleteSelected = async () => {
    const ids = Array.from(selectedRows)
    if (!confirm(`Delete ${ids.length} record(s)?`)) return
    for (const id of ids) {
      await scopedClient.records.delete(tableName, id)
    }
    setSelectedRows(new Set())
    await recordsState.refresh()
  }

  const handleUpdateRecord = async (recordId: string, data: Record<string, unknown>) => {
    if (onUpdateOptions?.updateVia === "function") {
      await scopedClient.functions.runs.create(onUpdateOptions.updateFunctionName ?? tableName, {
        input: { ...data, id: recordId, record_id: recordId },
      })
    } else {
      await scopedClient.records.update(tableName, recordId, data)
    }
    await recordsState.refresh()
  }

  const runQuickAction = React.useCallback(async (
    action: RecordQuickAction,
    record: Record<string, unknown>,
    index: number,
    scope: "row" | "detail" = "row",
  ) => {
    const recordId = getRecordId(record)
    const actionKey = recordQuickActionKey(action, recordId, index)
    const mode = resolveRecordActionMode(action, quickActionMode)
    const context = { tableName, scope, record, recordId }
    const nextValues = resolveRecordActionValues(action, record)

    setSubmittingQuickActionKey(actionKey)
    setActionError(null)
    try {
      if (mode === "function") {
        const functionName = action.functionName
        if (!functionName) throw new Error(`Quick action "${action.label}" requires functionName in function mode.`)
        await scopedClient.functions.runs.create(functionName, {
          input: createRecordActionInput(action, context),
        })
      } else if (mode === "workflow") {
        const workflowName = action.workflowName
        if (!workflowName) throw new Error(`Quick action "${action.label}" requires workflowName in workflow mode.`)
        await scopedClient.workflows.runs.start(workflowName, createRecordActionInput(action, context))
      } else {
        await scopedClient.records.update(tableName, recordId, nextValues)
      }
      await recordsState.refresh()
      setDetailRecordId(recordId)
      onQuickActionSuccess?.({
        action,
        record,
        recordId,
        tableName,
        scope,
      })
    } catch (error) {
      setActionError(error instanceof Error ? error.message : `Action "${action.label}" failed.`)
    } finally {
      setSubmittingQuickActionKey(null)
    }
  }, [
    getRecordId,
    onQuickActionSuccess,
    quickActionMode,
    recordsState,
    scopedClient,
    tableName,
  ])

  const runBulkAction = React.useCallback(async (
    action: RecordQuickAction,
    index: number,
  ) => {
    if (selectedRecordsList.length === 0) return

    const recordIds = selectedRecordsList.map(getRecordId).filter(Boolean)
    const actionKey = recordActionKey(action, `bulk:${recordIds.join(",")}`, index)
    const mode = resolveRecordActionMode(action, quickActionMode)
    const context = {
      tableName,
      scope: "bulk" as const,
      records: selectedRecordsList,
      recordIds,
    }

    setSubmittingBulkActionKey(actionKey)
    setActionError(null)
    try {
      if (mode === "function") {
        const functionName = action.functionName
        if (!functionName) throw new Error(`Bulk action "${action.label}" requires functionName in function mode.`)
        await scopedClient.functions.runs.create(functionName, {
          input: createRecordActionInput(action, context),
        })
      } else if (mode === "workflow") {
        const workflowName = action.workflowName
        if (!workflowName) throw new Error(`Bulk action "${action.label}" requires workflowName in workflow mode.`)
        await scopedClient.workflows.runs.start(workflowName, createRecordActionInput(action, context))
      } else {
        for (const record of selectedRecordsList) {
          const recordId = getRecordId(record)
          const nextValues = resolveRecordActionValues(action, record)
          if (Object.keys(nextValues).length > 0) {
            await scopedClient.records.update(tableName, recordId, nextValues)
          }
        }
      }

      setSelectedRows(new Set())
      await recordsState.refresh()
      onQuickActionSuccess?.({
        action,
        tableName,
        scope: "bulk",
        records: selectedRecordsList,
        recordIds,
      })
    } catch (error) {
      setActionError(error instanceof Error ? error.message : `Bulk action "${action.label}" failed.`)
    } finally {
      setSubmittingBulkActionKey(null)
    }
  }, [
    getRecordId,
    onQuickActionSuccess,
    quickActionMode,
    recordsState,
    scopedClient,
    selectedRecordsList,
    tableName,
  ])

  const handleResolveForeignKeyLabels = React.useCallback((columnName: string, labels: Record<string, string>) => {
    setForeignKeyLabelMap((previous) => {
      if (shallowEqualLabelMap(previous[columnName], labels)) return previous
      return { ...previous, [columnName]: labels }
    })
  }, [])

  const detailActionContent = React.useMemo(() => {
    if (!table || !detailRecord) return customDetailActionContent

    const quickActionContent = detailQuickActionsEnabled && quickActions?.length ? (
      <RecordQuickActionButtons
        record={detailRecord}
        recordId={getRecordId(detailRecord)}
        actions={quickActions}
        pendingActionKey={submittingQuickActionKey}
        onRun={(action, index) => void runQuickAction(action, detailRecord, index, "detail")}
      />
    ) : null

    const pager = detailMode === "inline" ? (
      <div className="flex items-center gap-1">
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={() => {
            if (detailRecordIndex > 0) {
              setDetailRecordId(getRecordId(displayedRecords[detailRecordIndex - 1]))
            }
          }}
          disabled={detailRecordIndex <= 0}
        >
          <ChevronLeft />
          <span className="sr-only">Previous record</span>
        </Button>
        <Button
          type="button"
          variant="ghost"
          size="icon-sm"
          onClick={() => {
            if (detailRecordIndex >= 0 && detailRecordIndex < displayedRecords.length - 1) {
              setDetailRecordId(getRecordId(displayedRecords[detailRecordIndex + 1]))
            }
          }}
          disabled={detailRecordIndex < 0 || detailRecordIndex >= displayedRecords.length - 1}
        >
          <ChevronRight />
          <span className="sr-only">Next record</span>
        </Button>
      </div>
    ) : null

    if (!customDetailActionContent && !quickActionContent && !pager) return null

    return (
      <div className={cn("flex min-w-0 flex-wrap items-center gap-2", detailMode === "inline" ? "justify-end" : "justify-start")}>
        {customDetailActionContent ? (
          <div className="min-w-0 max-w-full overflow-x-auto pb-1">
            {customDetailActionContent}
          </div>
        ) : null}
        {quickActionContent ? (
          <div className="min-w-0 max-w-full overflow-x-auto pb-1">
            {quickActionContent}
          </div>
        ) : null}
        {pager}
      </div>
    )
  }, [
    customDetailActionContent,
    detailMode,
    detailQuickActionsEnabled,
    detailRecord,
    detailRecordIndex,
    displayedRecords,
    getRecordId,
    quickActions,
    runQuickAction,
    submittingQuickActionKey,
    table,
  ])

  if (tableState.isLoading) {
    return (
      <div className={cn("flex flex-col gap-4 p-6", recordsSurfaceClassName(resolvedSurface, radius))}>
        <div className="flex items-center gap-3">
          <Skeleton className="size-7 rounded-md" />
          <div className="space-y-1.5">
            <Skeleton className="h-4 w-32" />
            <Skeleton className="h-3 w-20" />
          </div>
        </div>
        <RecordsSkeletonGrid columnCount={4} rowCount={6} density={density} />
      </div>
    )
  }

  if (!table) {
    return (
      <div className={cn("flex h-64 flex-col items-center justify-center px-6 text-center", recordsSurfaceClassName(resolvedSurface, radius, true))}>
        <p className="text-lg font-semibold text-foreground">Table not found</p>
        <p className="mt-1 text-sm text-muted-foreground">The table &quot;{tableName}&quot; could not be loaded.</p>
      </div>
    )
  }

  const recordsCanvas = (
    <>
      {recordsState.error ? (
        <RecordsErrorState error={recordsState.error} radius={radius} onRetry={() => recordsState.refresh()} />
      ) : viewMode === "grid" ? (
        <div className={cn("overflow-auto", recordsSurfaceClassName(resolvedSurface, radius))}>
          <DataTable className="min-w-full table-fixed">
            <TableHeader className={cn("sticky top-0 z-10 backdrop-blur-md", resolvedSurface === "inherit" ? "border-b border-border/15 bg-background/80" : resolvedSurface === "muted" ? "border-b border-border/20 bg-background/85" : "border-b border-border/30 bg-card/95")}>
              <TableRow className="hover:bg-transparent">
                {selectionEnabled ? (
                  <TableHead className="w-10 px-2 py-2 text-center">
                    <Checkbox
                      checked={allDisplayedSelected}
                      onCheckedChange={handleSelectAll}
                      className="h-4 w-4 rounded"
                    />
                  </TableHead>
                ) : null}
                {resolvedColumns.map((col) => (
                  <TableHead
                    key={col.name}
                    className={cn("cursor-pointer select-none px-3 text-left text-xs font-medium tracking-wide text-muted-foreground transition-colors hover:text-foreground", density === "compact" ? "py-2" : density === "spacious" ? "py-3.5" : "py-2.5")}
                    style={columnWidthStyle(columnWidths?.[col.name])}
                    onClick={() => handleSortColumn(col.name)}
                  >
                    <div className="flex items-center gap-1.5">
                      <span>{columnLabels?.[col.name] ?? col.name.replace(/_/g, " ")}</span>
                      {sortField === col.name ? (
                        sortOrder === "asc" ? <ArrowUp className="size-3" /> : <ArrowDown className="size-3" />
                      ) : null}
                      {showTypeHints && (
                        <span className={typeBadgeClasses(col)}>
                          {col.foreign_key ? "ref" : col.type.toLowerCase()}
                        </span>
                      )}
                    </div>
                  </TableHead>
                ))}
                <TableHead className="w-10 px-2 py-2" />
              </TableRow>
            </TableHeader>
            <TableBody className="divide-y divide-border/20">
              {recordsState.isLoading ? (
                <RecordsSkeletonTableRows columnCount={resolvedColumns.length + (selectionEnabled ? 2 : 1)} rowCount={8} density={density} />
              ) : displayedRecords.length === 0 ? (
                <RecordsTableMessage colSpan={resolvedColumns.length + (selectionEnabled ? 2 : 1)}>
                  <EmptyRecordsState
                    constrained={hasActiveConstraints}
                    emptyState={emptyState}
                    radius={radius}
                    onClear={hasActiveConstraints ? clearAllConstraints : undefined}
                    onCreate={createEnabled ? handleCreateClick : undefined}
                  />
                </RecordsTableMessage>
              ) : (
                displayedRecords.map((record) => {
                  const id = getRecordId(record)
                  const selected = selectedRows.has(id)
                  return (
                    <TableRow
                      key={id}
                      data-state={selected ? "selected" : undefined}
                      className={cn(
                        "group transition-colors duration-75",
                        selected && "bg-primary/5",
                      )}
                    >
                      {selectionEnabled ? (
                        <TableCell className="px-2 py-1 text-center">
                          <Checkbox
                            checked={selected}
                            onCheckedChange={() => handleSelectRow(id)}
                            className={cn(
                              "h-4 w-4 rounded opacity-0 transition-opacity group-hover:opacity-100",
                              selected && "opacity-100",
                            )}
                          />
                        </TableCell>
                      ) : null}
                      {resolvedColumns.map((col) => (
                        <TableCell key={col.name} className="px-0 py-0" style={columnWidthStyle(columnWidths?.[col.name])}>
                          {renderCell ? (
                            renderCell(record, col, record[col.name])
                          ) : (
                            <EditableCell
                              value={record[col.name]}
                              column={col}
                              foreignKeyLabelMap={foreignKeyLabelMap[col.name]}
                              enumColorMap={enumColorMap}
                              onSave={async (newValue) => {
                                await handleUpdateRecord(id, { [col.name]: newValue })
                              }}
                            />
                          )}
                        </TableCell>
                      ))}
                      <TableCell className="px-2 py-1">
                        <Button
                          variant="ghost"
                          size="icon"
                          onClick={() => handleRecordClick(record)}
                          className="size-7 text-muted-foreground opacity-0 transition-all group-hover:opacity-100"
                        >
                          <MoreVertical className="size-3.5" />
                          <span className="sr-only">Open record details</span>
                        </Button>
                      </TableCell>
                    </TableRow>
                  )
                })
              )}
            </TableBody>
          </DataTable>
        </div>
      ) : recordsState.isLoading ? (
        <RecordsSkeletonList rowCount={6} density={density} radius={radius} selectionEnabled={selectionEnabled} />
      ) : displayedRecords.length === 0 && !isGroupedView ? (
        <EmptyRecordsState
          constrained={hasActiveConstraints}
          emptyState={emptyState}
          radius={radius}
          onClear={hasActiveConstraints ? clearAllConstraints : undefined}
          onCreate={createEnabled ? handleCreateClick : undefined}
        />
      ) : viewMode === "calendar" ? (
        <RecordsCalendarView
          records={displayedRecords}
          table={table}
          visibleColumns={resolvedColumns}
          primaryKey={pk}
          selectedRecords={selectedRows}
          selectionEnabled={selectionEnabled}
          onSelectRecord={selectionEnabled ? handleSelectRow : undefined}
          onRecordClick={handleRecordClick}
          foreignKeyLabelMap={foreignKeyLabelMap}
          columnLabels={columnLabels}
          displayOptions={effectiveListOptions}
          quickActions={previewQuickActionsEnabled ? quickActions : undefined}
          onQuickAction={previewQuickActionsEnabled ? (action, record, index) => void runQuickAction(action, record, index) : undefined}
          pendingActionKey={submittingQuickActionKey}
          enumColorMap={enumColorMap}
          appearance={resolvedAppearance}
          density={density}
          radius={radius}
          dateField={calendarField}
        />
      ) : viewMode === "timeline" ? (
        <RecordsTimelineView
          records={displayedRecords}
          table={table}
          visibleColumns={resolvedColumns}
          primaryKey={pk}
          selectedRecords={selectedRows}
          selectionEnabled={selectionEnabled}
          onSelectRecord={selectionEnabled ? handleSelectRow : undefined}
          onRecordClick={handleRecordClick}
          foreignKeyLabelMap={foreignKeyLabelMap}
          columnLabels={columnLabels}
          displayOptions={effectiveListOptions}
          quickActions={previewQuickActionsEnabled ? quickActions : undefined}
          onQuickAction={previewQuickActionsEnabled ? (action, record, index) => void runQuickAction(action, record, index) : undefined}
          pendingActionKey={submittingQuickActionKey}
          enumColorMap={enumColorMap}
          appearance={resolvedAppearance}
          density={density}
          radius={radius}
          dateField={timelineField}
        />
      ) : viewMode === "matrix" ? (
        <RecordsMatrixView
          records={displayedRecords}
          table={table}
          visibleColumns={resolvedColumns}
          primaryKey={pk}
          selectedRecords={selectedRows}
          selectionEnabled={selectionEnabled}
          onSelectRecord={selectionEnabled ? handleSelectRow : undefined}
          onRecordClick={handleRecordClick}
          foreignKeyLabelMap={foreignKeyLabelMap}
          columnLabels={columnLabels}
          displayOptions={effectiveListOptions}
          quickActions={previewQuickActionsEnabled ? quickActions : undefined}
          onQuickAction={previewQuickActionsEnabled ? (action, record, index) => void runQuickAction(action, record, index) : undefined}
          pendingActionKey={submittingQuickActionKey}
          enumColorMap={enumColorMap}
          appearance={resolvedAppearance}
          density={density}
          radius={radius}
          rowField={matrixRowsBy}
          columnField={matrixColumnsBy}
        />
      ) : viewMode === "list" ? (
        <ListView
          records={displayedRecords}
          table={table}
          visibleColumns={resolvedColumns}
          selectedRecords={selectedRows}
          selectionEnabled={selectionEnabled}
          onSelectRecord={selectionEnabled ? handleSelectRow : undefined}
          onRecordClick={handleRecordClick}
          renderCard={renderCard}
          foreignKeyLabelMap={foreignKeyLabelMap}
          columnLabels={columnLabels}
          displayOptions={effectiveListOptions}
          quickActions={previewQuickActionsEnabled ? quickActions : undefined}
          onQuickAction={previewQuickActionsEnabled ? (action, record, index) => void runQuickAction(action, record, index) : undefined}
          pendingActionKey={submittingQuickActionKey}
          enumColorMap={enumColorMap}
          appearance={resolvedAppearance}
          surface={resolvedSurface}
          density={density}
          radius={radius}
        />
      ) : (viewMode === "kanban" || viewMode === "linear") && groupByColumn ? (
        <GroupedView
          records={displayedRecords}
          groupByColumn={groupByColumn}
          layout={viewMode}
          primaryKey={pk}
          visibleColumns={resolvedColumns}
          selectedRecords={selectedRows}
          selectionEnabled={selectionEnabled}
          onSelectRecord={selectionEnabled ? handleSelectRow : undefined}
          onRecordClick={handleRecordClick}
          renderCard={renderCard}
          foreignKeyLabelMap={foreignKeyLabelMap}
          columnLabels={columnLabels}
          displayOptions={effectiveGroupedOptions}
          quickActions={previewQuickActionsEnabled ? quickActions : undefined}
          onQuickAction={previewQuickActionsEnabled ? (action, record, index) => void runQuickAction(action, record, index) : undefined}
          pendingActionKey={submittingQuickActionKey}
          enumColorMap={enumColorMap}
          appearance={resolvedAppearance}
          density={density}
          radius={radius}
        />
      ) : null}

      {paginationMode === "infinite" && !recordsState.isLoading ? (
        <div ref={setInfiniteSentinel} className="h-px" />
      ) : null}
    </>
  )

  return (
    <div
      data-appearance={appearance}
      data-surface={resolvedSurface}
      data-density={density}
      data-radius={radius}
      className={cn("lemma-records-view flex h-full min-h-0 flex-col", recordsRootClassName(resolvedSurface), className)}
    >
      {foreignKeyColumns.map((column) => (
        <ForeignKeyLabelResolver
          key={column.name}
          client={client}
          podId={podId}
          tableName={tableName}
          column={column}
          labelField={foreignKeyLabels?.[column.name]}
          onResolve={handleResolveForeignKeyLabels}
        />
      ))}

      <div className={cn("shrink-0 backdrop-blur-sm", recordsHeaderClassName(resolvedSurface))}>
        <div className={cn("flex flex-col lg:flex-row lg:items-center lg:justify-between", recordsToolbarClassName(density))}>
          <div className="min-w-0">
            <div className="flex items-center gap-2">
              <span className={cn("flex size-7 items-center justify-center border border-border/50 bg-muted/40 text-muted-foreground", recordsRadiusClassName(radius, "control"))}>
                <Database className="size-3.5" />
              </span>
              <div className="min-w-0">
                {titleEnabled ? (
                  <h1 className="truncate text-sm font-semibold text-foreground">
                    {title ?? table.name}
                  </h1>
                ) : null}
                <p className="text-xs text-muted-foreground">
                  {hasSearch ? `${displayedRecords.length} matching on this page` : `${total} records`}
                </p>
              </div>
            </div>
          </div>

          <div className="flex flex-wrap items-center gap-2">
            {searchEnabled ? (
              <div className="relative w-full sm:w-auto">
                <Search className="absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-muted-foreground" />
                <Input
                  value={search}
                  onChange={(e) => setSearch(e.target.value)}
                  placeholder={searchPlaceholder}
                  className={cn("h-8 w-full pl-8 pr-8 text-xs sm:w-56", recordsRadiusClassName(radius, "control"))}
                />
                {search && (
                  <button
                    type="button"
                    onClick={clearSearch}
                    className={cn("absolute right-2 top-1/2 flex size-4 -translate-y-1/2 items-center justify-center text-muted-foreground transition-colors hover:bg-muted hover:text-foreground", recordsRadiusClassName(radius, "control"))}
                  >
                    <X className="size-3" />
                    <span className="sr-only">Clear search</span>
                  </button>
                )}
              </div>
            ) : null}

            {searchEnabled && (viewSwitcherEnabled || filtersEnabled || headerActions || createEnabled) ? (
              <div className={cn("mx-1 h-5 w-px bg-border/50", appearance === "borderless" && "hidden")} />
            ) : null}

            {viewSwitcherEnabled && availableViewModes.length > 1 ? (
              <ViewModeToggle mode={viewMode} onChange={setViewMode} availableModes={availableViewModes} radius={radius} />
            ) : null}

            {filtersEnabled ? (
              <Button
                variant="ghost"
                size="sm"
                onClick={() => setShowFilterBuilder(true)}
                className="h-8 gap-2 text-xs"
              >
                <Filter className="h-3.5 w-3.5 text-muted-foreground" />
                Filter{filters.length > 0 ? ` (${filters.length})` : ""}
              </Button>
            ) : null}

            {hasActiveConstraints && (
              <Button
                variant="ghost"
                size="sm"
                onClick={clearAllConstraints}
                className="h-8 gap-2 text-xs text-muted-foreground"
              >
                <X className="h-3.5 w-3.5" />
                Clear
              </Button>
            )}

            {headerActions}

            {createEnabled && (viewSwitcherEnabled || filtersEnabled || headerActions || hasActiveConstraints) ? (
              <div className={cn("mx-1 h-5 w-px bg-border/50", appearance === "borderless" && "hidden")} />
            ) : null}

            {createEnabled ? (
              <Button
                size="sm"
                onClick={handleCreateClick}
                className="h-8 gap-2 text-xs"
              >
                <Plus className="h-3.5 w-3.5" />
                New
              </Button>
            ) : null}
          </div>
        </div>

        {filtersEnabled && hasFilters && (
          <div className={cn("flex items-center gap-2 px-4 py-2 text-xs text-muted-foreground", appearance === "borderless" ? "border-t-0" : appearance === "minimal" ? "border-t border-border/15" : "border-t border-border/30")}>
            <span className="font-medium text-foreground">Filtered by</span>
            <div className="flex flex-wrap items-center gap-1.5">
              {filters.map((filter, index) => (
                <button
                  key={`${filter.field}-${filter.op}-${index}`}
                  type="button"
                  onClick={() => applyFilters(filters.filter((_, i) => i !== index))}
                  className="inline-flex items-center gap-1 rounded-full border border-border/60 bg-background px-2 py-0.5 text-foreground transition-colors hover:bg-muted/60"
                >
                  <span>{columnLabels?.[filter.field] ?? filter.field}</span>
                  <span className="text-muted-foreground">{filter.op}</span>
                  {filter.value != null && filter.value !== "" && <span>{String(filter.value)}</span>}
                  <X className="size-3 text-muted-foreground" />
                </button>
              ))}
            </div>
          </div>
        )}
      </div>

      {actionError ? (
        <div className="shrink-0 px-4 py-2">
          <div className={cn("flex items-start gap-2 border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive", recordsRadiusClassName(radius, "surface"))}>
            <AlertCircle className="mt-0.5 size-4 shrink-0" />
            <span>{actionError}</span>
          </div>
        </div>
      ) : null}

      {selectionEnabled && selectedRows.size > 0 && (
        <div className={cn("absolute left-1/2 top-20 z-30 flex -translate-x-1/2 items-center gap-4 rounded-full px-5 py-2.5 shadow-lg backdrop-blur-sm", recordsFloatingClassName(resolvedSurface))}>
          <span className="text-sm font-medium text-foreground">{selectedRows.size} selected</span>
          <div className="h-4 w-px bg-border" />
          {bulkActions?.length ? (
            <>
              <RecordQuickActionButtons
                record={{ selected_count: selectedRows.size }}
                recordId={`bulk:${Array.from(selectedRows).join(",")}`}
                actions={bulkActions}
                pendingActionKey={submittingBulkActionKey}
                onRun={(action, index) => void runBulkAction(action, index)}
                compact
              />
              <div className="h-4 w-px bg-border" />
            </>
          ) : null}
          <Button variant="ghost" size="sm" className="h-8 rounded-full" onClick={handleDeleteSelected}>
            <Trash2 className="mr-1.5 h-3.5 w-3.5 text-destructive" />
            Delete
          </Button>
        </div>
      )}

      {detailMode === "inline" ? (
        <div className="flex-1 min-h-0">
          <div
            className={cn(
              "grid h-full min-h-0 gap-3",
              density === "compact" ? "p-2" : density === "spacious" ? "p-4" : "p-3",
              "lg:grid-cols-[minmax(0,1fr)_minmax(26rem,36rem)]",
            )}
          >
            <div
              ref={contentRef}
              className={cn("min-h-0 overflow-auto", recordsSurfaceClassName(resolvedSurface, radius))}
            >
              {recordsCanvas}
            </div>
            <div className={cn("min-h-0 overflow-auto", recordsSurfaceClassName(resolvedSurface, radius))}>
              {detailRecord ? (
                <RecordDetail
                  record={detailRecord}
                  table={table}
                  client={client}
                  podId={podId}
                  mode={detailEditable ? "editable" : "view"}
                  variant={detailVariant}
                  tabs={detailTabs}
                  headerFields={detailHeaderFields}
                  fieldGroups={detailFieldGroups}
                  relatedRecords={detailRelatedRecords}
                  hiddenFields={hiddenFields}
                  titleField={detailTitleField}
                  descriptionField={detailDescriptionField}
                  identifierField={detailIdentifierField}
                  statusField={detailStatusField}
                  updateVia={onUpdateOptions?.updateVia}
                  updateFunctionName={onUpdateOptions?.updateFunctionName}
                  columnLabels={columnLabels}
                  foreignKeyLabels={foreignKeyLabels}
                  enumColorMap={enumColorMap}
                  appearance={resolvedAppearance === "minimal" ? "minimal" : "borderless"}
                  density={density}
                  radius={radius}
                  layout="embedded"
                  actions={detailActionContent}
                  tableLabel={detailTableLabel}
                  sectionLabels={detailSectionLabels}
                  sectionVisibility={detailSectionVisibility}
                  onRecordChanged={() => void recordsState.refresh()}
                  onDelete={async () => {
                    const id = getRecordId(detailRecord)
                    await scopedClient.records.delete(tableName, id)
                    setDetailRecordId(null)
                    await recordsState.refresh()
                  }}
                  className="h-full min-w-0"
                />
              ) : (
                <div className="flex h-full items-center justify-center px-6 text-center">
                  <div>
                    <p className="font-medium text-foreground">Select a record</p>
                    <p className="mt-1 text-sm text-muted-foreground">Choose a row or card to inspect details, related records, and available actions.</p>
                  </div>
                </div>
              )}
            </div>
          </div>
        </div>
      ) : (
        <div ref={contentRef} className={cn("flex-1 overflow-auto", recordsContentClassName(density))}>
          {recordsCanvas}
        </div>
      )}

      {footerEnabled ? (
        <div className={cn("shrink-0 px-4", recordsFooterClassName(resolvedSurface, density))}>
          <div className="flex items-center justify-between text-xs text-muted-foreground">
            <span>
              {progressivePagination
                ? hasSearch
                  ? `Showing ${displayedRecords.length} matching loaded record(s)`
                  : `Loaded ${records.length} of ${total}`
                : hasSearch
                ? `Showing ${displayedRecords.length} matching record(s) on this page`
                : `Showing ${pageStart}–${pageEnd} of ${total}`}
            </span>
            <div className="flex items-center gap-2">
              {progressivePagination ? (
                <Button
                  variant="ghost"
                  size="sm"
                  className="h-7 text-xs"
                  onClick={() => void handleLoadMore()}
                  disabled={!canLoadMore || recordsState.isLoadingMore}
                >
                  {recordsState.isLoadingMore ? <RefreshCw className="mr-1.5 size-3 animate-spin" /> : null}
                  {canLoadMore ? "Load more" : "All loaded"}
                </Button>
              ) : (
                <>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => setPage(Math.max(0, page - 1))}
                    disabled={page === 0}
                  >
                    Previous
                  </Button>
                  <Button
                    variant="ghost"
                    size="sm"
                    className="h-7 text-xs"
                    onClick={() => setPage(page + 1)}
                    disabled={page * pageSize + records.length >= total}
                  >
                    Next
                  </Button>
                </>
              )}
            </div>
          </div>
        </div>
      ) : null}

      {filtersEnabled && showFilterBuilder && table && (
        <FilterBuilder
          columns={resolvedColumns}
          filters={filters}
          onApply={applyFilters}
          onClose={() => setShowFilterBuilder(false)}
        />
      )}

      {detailMode !== "inline" && detailRecord && table && (
        <DetailSheet
          record={detailRecord}
          table={table}
          client={client}
          podId={podId}
          mode={detailMode === "modal" ? "modal" : "sheet"}
          variant={detailVariant}
          tabs={detailTabs}
          headerFields={detailHeaderFields}
          fieldGroups={detailFieldGroups}
          relatedRecords={detailRelatedRecords}
          editable={detailEditable}
          hiddenFields={hiddenFields}
          titleField={detailTitleField}
          descriptionField={detailDescriptionField}
          identifierField={detailIdentifierField}
          statusField={detailStatusField}
          onClose={() => setDetailRecordId(null)}
          onRecordChanged={() => void recordsState.refresh()}
          updateVia={onUpdateOptions?.updateVia}
          updateFunctionName={onUpdateOptions?.updateFunctionName}
          onDelete={async () => {
            const id = getRecordId(detailRecord)
            await scopedClient.records.delete(tableName, id)
            setDetailRecordId(null)
            await recordsState.refresh()
          }}
          onNext={() => {
            if (detailRecordIndex >= 0 && detailRecordIndex < displayedRecords.length - 1) {
              setDetailRecordId(getRecordId(displayedRecords[detailRecordIndex + 1]))
            }
          }}
          onPrevious={() => {
            if (detailRecordIndex > 0) setDetailRecordId(getRecordId(displayedRecords[detailRecordIndex - 1]))
          }}
          hasPrevious={detailRecordIndex > 0}
          hasNext={detailRecordIndex >= 0 && detailRecordIndex < displayedRecords.length - 1}
          columnLabels={columnLabels}
          foreignKeyLabels={foreignKeyLabels}
          enumColorMap={enumColorMap}
          appearance={resolvedAppearance}
          density={density}
          radius={radius}
          actions={detailActionContent}
          tableLabel={detailTableLabel}
          sectionLabels={detailSectionLabels}
          sectionVisibility={detailSectionVisibility}
        />
      )}

      {showCreateForm && table && (
        <RecordFormSheet
          client={client}
          podId={podId}
          tableName={tableName}
          table={table}
          submitVia={onCreateOptions?.submitVia}
          submitFunctionName={onCreateOptions?.submitFunctionName}
          hiddenFields={onCreateOptions?.hiddenFields ?? hiddenFields}
          fieldOrder={onCreateOptions?.fieldOrder}
          fieldGroups={onCreateOptions?.fieldGroups}
          fieldVisibility={onCreateOptions?.fieldVisibility}
          sectionVisibility={onCreateOptions?.sectionVisibility}
          foreignKeyLabels={foreignKeyLabels}
          enumColorMap={enumColorMap}
          mode={createMode === "modal" ? "modal" : "sheet"}
          appearance={resolvedAppearance}
          density={density}
          radius={radius}
          onClose={() => setShowCreateForm(false)}
          onSuccess={() => {
            setShowCreateForm(false)
            recordsState.refresh()
          }}
        />
      )}
    </div>
  )
}

function normalizeViewMode(mode: ViewMode): ResolvedViewMode {
  return mode === "grouped" ? "kanban" : mode
}

function resolveRecordColumns(
  table: Table | null | undefined,
  options: {
    visibleColumnNames?: string[]
    hiddenFields: string[]
    pinnedColumns?: string[]
    primaryField?: string
    defaultVisibleColumnCount: number
    showSystemFields: boolean
  },
): ColumnSchema[] {
  if (!table) return []

  const candidates = table.columns
    .filter((column) => !options.hiddenFields.includes(column.name))
    .filter((column) => column.type !== "VECTOR")

  if (options.visibleColumnNames?.length) {
    return orderColumns(
      options.visibleColumnNames
        .map((name) => candidates.find((column) => column.name === name))
        .filter((column): column is ColumnSchema => Boolean(column)),
      options,
    )
  }

  const visible = candidates
    .filter((column) => options.showSystemFields || !isSystemField(column))
    .filter((column) => column.name !== table.primary_key_column && column.name !== "id")

  const fallback = visible.length > 0 ? visible : candidates
  return orderColumns(fallback, options).slice(0, Math.max(1, options.defaultVisibleColumnCount))
}

function orderColumns(
  columns: ColumnSchema[],
  options: {
    pinnedColumns?: string[]
    primaryField?: string
  },
): ColumnSchema[] {
  const pinnedNames = uniqueStrings([
    ...(options.primaryField ? [options.primaryField] : []),
    ...(options.pinnedColumns ?? []),
  ])
  const pinned = pinnedNames
    .map((name) => columns.find((column) => column.name === name))
    .filter((column): column is ColumnSchema => Boolean(column))
  const remaining = columns.filter((column) => !pinnedNames.includes(column.name))
  return [...pinned, ...remaining]
}

function resolveDefaultSort(
  table: Table | null | undefined,
  defaultSort?: { field: string; order?: "asc" | "desc" },
): { field: string; order: "asc" | "desc" } | null {
  if (!table) return null
  const names = new Set(table.columns.map((column) => column.name))
  if (defaultSort && names.has(defaultSort.field)) {
    return { field: defaultSort.field, order: defaultSort.order ?? "asc" }
  }

  const updatedAt = table.columns.find((column) => column.name === "updated_at")
  if (updatedAt) return { field: updatedAt.name, order: "desc" }

  const createdAt = table.columns.find((column) => column.name === "created_at")
  if (createdAt) return { field: createdAt.name, order: "desc" }

  const titleLike = table.columns.find((column) => ["title", "name", "subject", "label"].includes(column.name))
  if (titleLike) return { field: titleLike.name, order: "asc" }

  return null
}

function columnWidthStyle(width: string | number | undefined): React.CSSProperties | undefined {
  if (typeof width === "undefined") return undefined
  return { width: typeof width === "number" ? `${width}px` : width }
}

function resolveAvailableViewModes(
  availableViews: ViewMode[] | undefined,
  defaultView: ViewMode,
  hasGroupBy: boolean,
): ResolvedViewMode[] {
  const requested: ViewMode[] = availableViews?.length
    ? availableViews
    : [defaultView]

  const normalized = requested
    .map((mode) => normalizeViewMode(mode))
    .filter((mode, index, allModes) => allModes.indexOf(mode) === index)

  const filtered = normalized.filter((mode) => (mode === "kanban" || mode === "linear" ? hasGroupBy : true))
  return filtered.length > 0 ? filtered : ["grid"]
}

function pickExistingColumnNames(
  table: Table | null | undefined,
  candidates: string[] | undefined,
): string[] | undefined {
  if (!table || !candidates?.length) return undefined
  const names = new Set(table.columns.map((column) => column.name))
  const picked = uniqueStrings(candidates.filter((candidate) => names.has(candidate)))
  return picked.length > 0 ? picked : undefined
}

function pickFirstExistingColumnName(
  table: Table | null | undefined,
  candidates: string[] | undefined,
): string | undefined {
  if (!table || !candidates?.length) return undefined
  const names = new Set(table.columns.map((column) => column.name))
  return candidates.find((candidate) => names.has(candidate))
}

function uniqueStrings(values: string[]): string[] {
  return values.filter((value, index, array) => value.length > 0 && array.indexOf(value) === index)
}

function resolveLegacyDetailTabs(
  tabs: LegacyCompatibleRecordDetailTab[] | undefined,
  options: {
    renderFiles?: (context: RecordDetailTabContext) => React.ReactNode
    renderComments?: (context: RecordDetailTabContext) => React.ReactNode
    renderActivity?: (context: RecordDetailTabContext) => React.ReactNode
  },
): RecordDetailTab[] | undefined {
  const legacyTabs = buildLegacyDetailExtensionTabs(options)

  if (!tabs?.length) {
    if (legacyTabs.length === 0) return undefined
    return [
      "details",
      ...legacyTabs,
    ]
  }

  const resolved = tabs.flatMap((tab) => {
    if (typeof tab !== "string") return [tab]
    if (tab === "details" || tab === "related") return [tab]
    const legacyTab = legacyTabs.find((candidate) => recordDetailTabKey(candidate) === tab)
    return legacyTab ? [legacyTab] : []
  })

  const deduped = resolved.filter(
    (tab, index, allTabs) =>
      allTabs.findIndex((candidate) => recordDetailTabKey(candidate) === recordDetailTabKey(tab)) === index,
  )

  return deduped.length > 0 ? deduped : undefined
}

function buildLegacyDetailExtensionTabs(options: {
  renderFiles?: (context: RecordDetailTabContext) => React.ReactNode
  renderComments?: (context: RecordDetailTabContext) => React.ReactNode
  renderActivity?: (context: RecordDetailTabContext) => React.ReactNode
}): RecordDetailTab[] {
  const tabs: RecordDetailTab[] = []

  if (options.renderComments) {
    tabs.push({
      id: "comments",
      label: "Comments",
      render: options.renderComments,
    })
  }

  if (options.renderActivity) {
    tabs.push({
      id: "activity",
      label: "Activity",
      render: options.renderActivity,
    })
  }

  if (options.renderFiles) {
    tabs.push({
      id: "files",
      label: "Files",
      render: options.renderFiles,
    })
  }

  return tabs
}

function recordDetailTabKey(tab: RecordDetailTab): string {
  return typeof tab === "string" ? tab : tab.id
}

function navigateTo(path: string): void {
  if (typeof window !== "undefined") {
    window.location.assign(path)
  }
}

function normalizeRecordFilters(filters: RecordFilter[]): RecordFilter[] {
  return filters.map((filter) => {
    const value = typeof filter.value === "string" ? filter.value.trim() : filter.value

    if (filter.op === "ilike" && typeof value === "string") {
      return { ...filter, value: `%${value}%` }
    }

    if (filter.op === "starts_with" && typeof value === "string") {
      return { ...filter, op: "ilike", value: `${value}%` }
    }

    if (filter.op === "ends_with" && typeof value === "string") {
      return { ...filter, op: "ilike", value: `%${value}` }
    }

    if (filter.op === "in" && typeof value === "string") {
      return {
        field: filter.field,
        op: "in",
        values: value.split(",").map((entry) => entry.trim()).filter(Boolean),
      }
    }

    if (filter.op === "is" || filter.op === "is not") {
      return { ...filter, value: null }
    }

    return { ...filter, value }
  })
}

function shallowEqualLabelMap(
  a: Record<string, string> | undefined,
  b: Record<string, string>,
): boolean {
  const aKeys = Object.keys(a ?? {})
  const bKeys = Object.keys(b)
  if (aKeys.length !== bKeys.length) return false
  return bKeys.every((key) => a?.[key] === b[key])
}

function ForeignKeyLabelResolver({
  client,
  podId,
  tableName,
  column,
  labelField,
  onResolve,
}: {
  client: LemmaClient
  podId?: string
  tableName: string
  column: ColumnSchema
  labelField?: string
  onResolve: (columnName: string, labels: Record<string, string>) => void
}) {
  const options = useForeignKeyOptions({
    client,
    podId,
    tableName,
    columnName: column.name,
    labelField,
    limit: 250,
    enabled: !!column.foreign_key,
  })

  React.useEffect(() => {
    const labels: Record<string, string> = {}
    for (const option of options.options) {
      labels[String(option.value)] = option.label
    }
    onResolve(column.name, labels)
  }, [column.name, onResolve, options.options])

  return null
}

function RecordsTableMessage({
  colSpan,
  children,
}: {
  colSpan: number
  children: React.ReactNode
}) {
  return (
    <TableRow className="hover:bg-transparent">
      <TableCell colSpan={colSpan} className="px-4 py-12">
        <div className="flex items-center justify-center gap-2 text-sm text-muted-foreground">
          {children}
        </div>
      </TableCell>
    </TableRow>
  )
}

function RecordsSkeletonGrid({
  columnCount,
  rowCount,
  density,
}: {
  columnCount: number
  rowCount: number
  density: LemmaRecordsDensity
}) {
  const rowH = density === "compact" ? "h-8" : density === "spacious" ? "h-12" : "h-10"
  return (
    <div className="space-y-2">
      <div className={cn("grid gap-2", `grid-cols-${columnCount}`)}>
        {Array.from({ length: columnCount }).map((_, i) => (
          <Skeleton key={i} className={cn("h-4", i === 0 ? "w-16" : i === columnCount - 1 ? "w-12" : "w-24")} />
        ))}
      </div>
      {Array.from({ length: rowCount }).map((_, ri) => (
        <div key={ri} className={cn("grid gap-2 items-center", `grid-cols-${columnCount}`, rowH)}>
          {Array.from({ length: columnCount }).map((_, ci) => (
            <Skeleton key={ci} className={cn("h-5 rounded", ci === 0 ? "w-12" : ci % 3 === 0 ? "w-20" : "w-full")} />
          ))}
        </div>
      ))}
    </div>
  )
}

function RecordsSkeletonTableRows({
  columnCount,
  rowCount,
  density,
}: {
  columnCount: number
  rowCount: number
  density: LemmaRecordsDensity
}) {
  const cellH = density === "compact" ? "h-6" : density === "spacious" ? "h-8" : "h-7"
  return (
    <>
      {Array.from({ length: rowCount }).map((_, ri) => (
        <TableRow key={ri} className="hover:bg-transparent">
          {Array.from({ length: columnCount }).map((_, ci) => (
            <TableCell key={ci} className="px-2 py-1">
              <Skeleton className={cn(cellH, "w-full rounded", ci === 0 && "w-5", ci === columnCount - 1 && "w-7")} />
            </TableCell>
          ))}
        </TableRow>
      ))}
    </>
  )
}

function RecordsSkeletonList({
  rowCount,
  density,
  radius,
  selectionEnabled = true,
}: {
  rowCount: number
  density: LemmaRecordsDensity
  radius: LemmaRecordsRadius
  selectionEnabled?: boolean
}) {
  return (
    <div className={cn("flex flex-col", density === "compact" ? "gap-1.5" : density === "spacious" ? "gap-3" : "gap-2")}>
      {Array.from({ length: rowCount }).map((_, i) => (
        <div
          key={i}
          className={cn(
            "flex items-start gap-3 border border-border/30 bg-card",
            recordsRadiusClassName(radius, "surface"),
            density === "compact" ? "p-3" : density === "spacious" ? "p-5" : "p-4",
          )}
        >
          {selectionEnabled ? <Skeleton className="size-4 mt-1 rounded" /> : null}
          <div className="flex-1 space-y-2">
            <Skeleton className="h-4 w-2/5" />
            <div className="flex gap-3">
              <Skeleton className="h-3 w-16" />
              <Skeleton className="h-3 w-20" />
              <Skeleton className="h-3 w-12" />
            </div>
          </div>
        </div>
      ))}
    </div>
  )
}

function RecordsErrorState({ error, radius, onRetry }: { error: Error; radius: LemmaRecordsRadius; onRetry: () => void }) {
  return (
    <div className={cn("flex min-h-64 flex-col items-center justify-center gap-3 border border-destructive/30 bg-destructive/5 px-6 text-center", recordsRadiusClassName(radius, "surface"))}>
      <div className={cn("flex size-10 items-center justify-center bg-destructive/10 text-destructive", recordsRadiusClassName(radius, "pill"))}>
        <AlertCircle className="size-5" />
      </div>
      <div>
        <p className="font-medium text-foreground">Records could not be loaded</p>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{error.message}</p>
      </div>
      <Button variant="outline" size="sm" onClick={onRetry}>
        <RefreshCw className="mr-2 size-3.5" />
        Retry
      </Button>
    </div>
  )
}

function EmptyRecordsState({
  constrained,
  emptyState,
  radius,
  onClear,
  onCreate,
}: {
  constrained: boolean
  emptyState?: React.ReactNode
  radius: LemmaRecordsRadius
  onClear?: () => void
  onCreate?: () => void
}) {
  if (!constrained && emptyState) return <>{emptyState}</>

  return (
    <div className={cn("flex min-h-64 flex-col items-center justify-center gap-3 border border-dashed border-border bg-card/60 px-6 text-center", recordsRadiusClassName(radius, "surface"))}>
      <div className={cn("flex size-10 items-center justify-center border border-border/60 bg-muted/40 text-muted-foreground", recordsRadiusClassName(radius, "pill"))}>
        <Database className="size-5" />
      </div>
      <div>
        <p className="font-medium text-foreground">
          {constrained ? "No records match this view" : "No records yet"}
        </p>
        <p className="mt-1 max-w-sm text-sm text-muted-foreground">
          {constrained
            ? "Try clearing search or filters to broaden the table."
            : onCreate
              ? "Create the first record and this workspace will fill in automatically."
              : "Records will appear here as soon as data is added to this table."}
        </p>
      </div>
      <div className="flex items-center gap-2">
        {onClear && (
          <Button variant="outline" size="sm" onClick={onClear}>
            Clear view
          </Button>
        )}
        {onCreate ? (
          <Button size="sm" onClick={onCreate}>
            <Plus className="mr-2 size-3.5" />
            New record
          </Button>
        ) : null}
      </div>
    </div>
  )
}

function isSearchableColumn(column: ColumnSchema): boolean {
  return column.type !== "VECTOR"
}

function matchesSearchValue(value: unknown, query: string): boolean {
  if (value == null) return false
  if (typeof value === "string") return value.toLowerCase().includes(query)
  if (typeof value === "number" || typeof value === "boolean") {
    return String(value).toLowerCase().includes(query)
  }
  if (value instanceof Date) return value.toISOString().toLowerCase().includes(query)
  try {
    return JSON.stringify(value).toLowerCase().includes(query)
  } catch {
    return String(value).toLowerCase().includes(query)
  }
}

function ViewModeToggle({
  mode,
  onChange,
  availableModes,
  radius,
}: {
  mode: ResolvedViewMode
  onChange: (m: ResolvedViewMode) => void
  availableModes: ResolvedViewMode[]
  radius: LemmaRecordsRadius
}) {
  return (
    <div className={cn("flex items-center gap-0.5 border border-border/50 bg-muted/30 p-0.5", recordsRadiusClassName(radius, "control"))}>
      {availableModes.map((availableMode) => (
        <button
          key={availableMode}
          onClick={() => onChange(availableMode)}
          className={cn(
            "flex items-center gap-1.5 px-2 py-1 text-xs transition-colors",
            recordsRadiusClassName(radius, "control"),
            mode === availableMode
              ? "bg-card text-foreground shadow-sm"
              : "text-muted-foreground hover:bg-card/50 hover:text-foreground",
          )}
        >
          {availableMode === "grid" || availableMode === "kanban" ? <LayoutGrid className="h-3.5 w-3.5" /> : null}
          {availableMode === "list" ? <List className="h-3.5 w-3.5" /> : null}
          {availableMode === "linear" ? <Rows3 className="h-3.5 w-3.5" /> : null}
          {availableMode === "calendar" ? <Calendar className="h-3.5 w-3.5" /> : null}
          {availableMode === "timeline" ? <Rows3 className="h-3.5 w-3.5" /> : null}
          {availableMode === "matrix" ? <LayoutGrid className="h-3.5 w-3.5" /> : null}
          {viewModeLabel(availableMode)}
        </button>
      ))}
    </div>
  )
}

function viewModeLabel(mode: ResolvedViewMode): string {
  if (mode === "grid") return "Grid"
  if (mode === "list") return "List"
  if (mode === "kanban") return "Kanban"
  if (mode === "linear") return "Linear"
  if (mode === "calendar") return "Calendar"
  if (mode === "timeline") return "Timeline"
  return "Matrix"
}

function recordsRootClassName(surface: LemmaRecordsSurface) {
  if (surface === "card") return "bg-card"
  return "bg-transparent"
}

function recordsHeaderClassName(surface: LemmaRecordsSurface) {
  if (surface === "inherit") return "border-b border-border/15 bg-transparent"
  if (surface === "muted") return "border-b border-border/20 bg-background/70"
  return "border-b border-border/40 bg-card/95"
}

function recordsToolbarClassName(density: LemmaRecordsDensity) {
  if (density === "compact") return "gap-2 px-3 py-2"
  if (density === "spacious") return "gap-4 px-5 py-4"
  return "gap-3 px-4 py-3"
}

function recordsContentClassName(density: LemmaRecordsDensity) {
  if (density === "compact") return "p-2"
  if (density === "spacious") return "p-5"
  return "p-4"
}

function recordsFooterClassName(surface: LemmaRecordsSurface, density: LemmaRecordsDensity) {
  return cn(
    surface === "inherit"
      ? "border-t-0 bg-transparent"
      : surface === "muted"
        ? "border-t border-border/15 bg-transparent"
        : "border-t border-border/40 bg-card",
    density === "compact" ? "py-2" : density === "spacious" ? "py-3.5" : "py-2.5",
  )
}

function recordsFloatingClassName(surface: LemmaRecordsSurface) {
  if (surface === "inherit") return "bg-background/90 shadow-none ring-1 ring-border/15"
  if (surface === "muted") return "bg-background/95 shadow-none ring-1 ring-border/20"
  return "border border-border/50 bg-card/95"
}

function recordsSurfaceClassName(surface: LemmaRecordsSurface, radius: LemmaRecordsRadius, dashed = false) {
  return cn(
    surface === "inherit" ? "bg-transparent shadow-none" : surface === "muted" ? "bg-background/60 shadow-none" : "bg-card",
    recordsRadiusClassName(radius, "surface"),
    dashed ? "border-dashed" : null,
    surface === "inherit" ? (dashed ? "border border-dashed border-border/25" : "border-0 ring-0") : null,
    surface === "muted" ? (dashed ? "border border-dashed border-border/25" : "ring-1 ring-border/15") : null,
    surface === "card" ? "border border-border/50 shadow-sm" : null,
  )
}
