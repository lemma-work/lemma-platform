import * as React from "react"
import type { LemmaClient } from "lemma-sdk"
import {
  AlertCircle,
  ArrowUpRight,
  Bot,
  Database,
  FileText,
  Loader2,
  Search,
  X,
} from "lucide-react"
import { Badge } from "@/components/lemma/ui/badge"
import { Button } from "@/components/lemma/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogTitle,
} from "@/components/lemma/ui/dialog"
import { Input } from "@/components/lemma/ui/input"
import { Separator } from "@/components/lemma/ui/separator"
import { cn } from "@/components/lemma/lib/utils"

type SearchRecord = Record<string, unknown>
type ResultIcon = React.ComponentType<{ className?: string }>
type FileSearchOptions = Parameters<LemmaClient["files"]["search"]>[1]
type FileSearchMethod = NonNullable<FileSearchOptions>["searchMethod"]
type FileSearchResponse = Awaited<ReturnType<LemmaClient["files"]["search"]>>
type FileSearchResult = FileSearchResponse["results"][number]
type AgentConversation = Awaited<ReturnType<LemmaClient["conversations"]["createForAgent"]>>
type SearchSourceType = "table" | "files"
type SearchSourceStatus = "idle" | "loading" | "success" | "empty" | "error"

export type LemmaGlobalSearchOpenMode = "navigate" | "new-tab" | "callback" | "none"
export type LemmaGlobalSearchAppearance = "default" | "minimal" | "borderless" | "contained"
export type LemmaGlobalSearchDensity = "compact" | "comfortable" | "spacious"
export type LemmaGlobalSearchRadius = "none" | "sm" | "md" | "lg" | "xl"

export interface LemmaGlobalSearchResultSummary {
  key: string
  type: "record" | "file"
  sourceLabel: string
  title: string
  subtitle: string | null
  preview: string | null
}

export interface LemmaGlobalSearchResultContext {
  query: string
  result: LemmaGlobalSearchResultSummary
  close: () => void
}

export interface LemmaGlobalSearchAgentContext {
  query: string
  results: LemmaGlobalSearchResultSummary[]
  close: () => void
}

export interface LemmaGlobalSearchAgent {
  agentName: string
  assistantName?: string
  enabled?: boolean
  label?: string
  include?: "query" | "query-and-results"
  resultLimit?: number
  conversationTitle?: string | ((context: LemmaGlobalSearchAgentContext) => string)
  buildMessage?: (context: LemmaGlobalSearchAgentContext) => string
  href?: (conversation: AgentConversation, context: LemmaGlobalSearchAgentContext) => string | null | undefined
  onConversationCreated?: (
    conversation: AgentConversation,
    context: LemmaGlobalSearchAgentContext,
  ) => void | Promise<void>
  onError?: (error: Error) => void
}

export type LemmaGlobalSearchAssistant = LemmaGlobalSearchAgent

export interface LemmaGlobalSearchTable {
  tableName: string
  label: string
  searchFields: string[]
  displayField?: string
  subtitleField?: string
  limit?: number
  icon?: ResultIcon
  openMode?: LemmaGlobalSearchOpenMode
  href?: (record: SearchRecord, context: LemmaGlobalSearchResultContext) => string | null | undefined
  onSelect?: (record: SearchRecord, context: LemmaGlobalSearchResultContext) => void | Promise<void>
}

export interface LemmaGlobalSearchFiles {
  enabled?: boolean
  label?: string
  limit?: number
  searchMethod?: FileSearchMethod
  openMode?: LemmaGlobalSearchOpenMode
  href?: (result: FileSearchResult, context: LemmaGlobalSearchResultContext) => string | null | undefined
  onSelect?: (result: FileSearchResult, context: LemmaGlobalSearchResultContext) => void | Promise<void>
}

export interface LemmaGlobalSearchProps {
  client: LemmaClient
  podId?: string
  tables: LemmaGlobalSearchTable[]
  files?: LemmaGlobalSearchFiles
  agent?: LemmaGlobalSearchAgent
  assistant?: LemmaGlobalSearchAgent
  enabled?: boolean
  placeholder?: string
  minQueryLength?: number
  debounceMs?: number
  triggerLabel?: string
  appearance?: LemmaGlobalSearchAppearance
  density?: LemmaGlobalSearchDensity
  radius?: LemmaGlobalSearchRadius
  className?: string
  dialogClassName?: string
}

type SearchItem = LemmaGlobalSearchResultSummary & {
  icon: ResultIcon
  openMode?: LemmaGlobalSearchOpenMode
  resolveHref?: (context: LemmaGlobalSearchResultContext) => string | null | undefined
  runSelect?: (context: LemmaGlobalSearchResultContext) => void | Promise<void>
}

type SearchGroup = {
  key: string
  label: string
  type: SearchSourceType
  count: number
  items: SearchItem[]
}

type SearchSourceState = {
  key: string
  label: string
  type: SearchSourceType
  status: SearchSourceStatus
  count: number
  error: string | null
}

type AssistantState = {
  status: "idle" | "loading" | "success" | "error"
  message: string | null
}

export function LemmaGlobalSearch({
  client,
  podId,
  tables,
  files,
  agent,
  assistant,
  enabled = true,
  placeholder = "Search records and files...",
  minQueryLength = 2,
  debounceMs = 300,
  triggerLabel = "Search",
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  className,
  dialogClassName,
}: LemmaGlobalSearchProps) {
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState("")
  const [groups, setGroups] = React.useState<SearchGroup[]>([])
  const [sourceStates, setSourceStates] = React.useState<SearchSourceState[]>([])
  const [activeIndex, setActiveIndex] = React.useState(0)
  const [assistantState, setAssistantState] = React.useState<AssistantState>({ status: "idle", message: null })
  const requestRef = React.useRef(0)
  const inputRef = React.useRef<HTMLInputElement | null>(null)
  const trimmedQuery = query.trim()
  const debouncedQuery = useDebouncedValue(trimmedQuery, debounceMs)
  const scopedClient = React.useMemo(() => (podId ? client.withPod(podId) : client), [client, podId])
  const agentHandoff = agent ?? assistant
  const shortcutLabel = React.useMemo(() => {
    if (typeof navigator === "undefined") return "Ctrl K"
    return /Mac|iPhone|iPad/.test(navigator.platform) ? "⌘K" : "Ctrl K"
  }, [])

  const sources = React.useMemo(() => {
    const nextSources: SearchSourceState[] = tables.map((table) => ({
      key: `table:${table.tableName}`,
      label: table.label,
      type: "table",
      status: "idle",
      count: 0,
      error: null,
    }))

    if (files?.enabled) {
      nextSources.push({
        key: "files",
        label: files.label ?? "Files",
        type: "files",
        status: "idle",
        count: 0,
        error: null,
      })
    }

    return nextSources
  }, [files?.enabled, files?.label, tables])

  const resultGroups = React.useMemo(
    () =>
      sources
        .map((source) => groups.find((group) => group.key === source.key))
        .filter((group): group is SearchGroup => Boolean(group)),
    [groups, sources],
  )

  const flatItems = React.useMemo(
    () => resultGroups.flatMap((group) => group.items),
    [resultGroups],
  )

  const isLoading = sourceStates.some((source) => source.status === "loading")
  const hasSourceErrors = sourceStates.some((source) => source.status === "error")
  const hasValidQuery = debouncedQuery.length >= minQueryLength
  const showTypeHint = trimmedQuery.length > 0 && trimmedQuery.length < minQueryLength
  const hasResults = resultGroups.length > 0
  const assistantEnabled = Boolean((agentHandoff?.agentName ?? agentHandoff?.assistantName) && (agentHandoff.enabled ?? true))
  const assistantResults = React.useMemo(
    () => flatItems.map((item) => summarizeItem(item)),
    [flatItems],
  )

  React.useEffect(() => {
    if (!open) return
    const focusTimer = window.setTimeout(() => {
      inputRef.current?.focus()
      inputRef.current?.select()
    }, 20)
    return () => window.clearTimeout(focusTimer)
  }, [open])

  React.useEffect(() => {
    if (!enabled) return
    const handleKeyDown = (event: KeyboardEvent) => {
      const isShortcut = (event.metaKey || event.ctrlKey) && event.key.toLowerCase() === "k"
      if (!isShortcut) return
      event.preventDefault()
      setOpen(true)
    }

    window.addEventListener("keydown", handleKeyDown)
    return () => window.removeEventListener("keydown", handleKeyDown)
  }, [enabled])

  React.useEffect(() => {
    setActiveIndex(0)
    setAssistantState({ status: "idle", message: null })
  }, [debouncedQuery, open])

  React.useEffect(() => {
    setActiveIndex((current) => {
      if (flatItems.length === 0) return 0
      return Math.min(current, flatItems.length - 1)
    })
  }, [flatItems.length])

  React.useEffect(() => {
    if (!open || !enabled || debouncedQuery.length < minQueryLength) {
      requestRef.current += 1
      setGroups([])
      setSourceStates([])
      return
    }

    const requestId = requestRef.current + 1
    requestRef.current = requestId
    let cancelled = false

    setGroups([])
    setSourceStates(sources.map((source) => ({ ...source, status: "loading", count: 0, error: null })))

    for (const table of tables) {
      const sourceKey = `table:${table.tableName}`

      void (async () => {
        try {
          const group = await searchTableSource(scopedClient, table, debouncedQuery)
          if (cancelled || requestRef.current !== requestId) return

          setGroups((current) => upsertGroup(current, group))
          setSourceStates((current) =>
            updateSourceState(current, sourceKey, {
              status: group.count > 0 ? "success" : "empty",
              count: group.count,
              error: null,
            }),
          )
        } catch (searchError) {
          if (cancelled || requestRef.current !== requestId) return
          setSourceStates((current) =>
            updateSourceState(current, sourceKey, {
              status: "error",
              count: 0,
              error: normalizeMessage(searchError, `${table.label} search failed.`),
            }),
          )
        }
      })()
    }

    if (files?.enabled) {
      void (async () => {
        try {
          const response = await scopedClient.files.search(debouncedQuery, {
            limit: files.limit ?? 5,
            searchMethod: files.searchMethod,
          })

          const items = (response.results ?? []).map((result) => buildFileItem(result, files))
          const group = {
            key: "files",
            label: files.label ?? "Files",
            type: "files",
            count: items.length,
            items,
          } satisfies SearchGroup

          if (cancelled || requestRef.current !== requestId) return

          setGroups((current) => upsertGroup(current, group))
          setSourceStates((current) =>
            updateSourceState(current, "files", {
              status: group.count > 0 ? "success" : "empty",
              count: group.count,
              error: null,
            }),
          )
        } catch (searchError) {
          if (cancelled || requestRef.current !== requestId) return
          setSourceStates((current) =>
            updateSourceState(current, "files", {
              status: "error",
              count: 0,
              error: normalizeMessage(searchError, "File search failed."),
            }),
          )
        }
      })()
    }

    return () => {
      cancelled = true
    }
  }, [debouncedQuery, enabled, files, minQueryLength, open, scopedClient, sources, tables])

  const closeSearch = React.useCallback(() => {
    setOpen(false)
    setQuery("")
    setGroups([])
    setSourceStates([])
    setActiveIndex(0)
    setAssistantState({ status: "idle", message: null })
    requestRef.current += 1
  }, [])

  const handleOpenChange = (nextOpen: boolean) => {
    if (nextOpen) {
      setOpen(true)
      return
    }

    closeSearch()
  }

  const buildResultContext = React.useCallback(
    (item: SearchItem): LemmaGlobalSearchResultContext => ({
      query: debouncedQuery,
      result: summarizeItem(item),
      close: closeSearch,
    }),
    [closeSearch, debouncedQuery],
  )

  const handleSelect = React.useCallback(
    async (item: SearchItem) => {
      const context = buildResultContext(item)
      const href = item.resolveHref?.(context) ?? null
      const mode = item.openMode ?? (item.runSelect ? "callback" : href ? "navigate" : "none")

      await item.runSelect?.(context)

      if (mode === "navigate" && href) {
        window.location.assign(href)
        closeSearch()
        return
      }

      if (mode === "new-tab" && href) {
        window.open(href, "_blank", "noopener,noreferrer")
        closeSearch()
        return
      }

      if (mode !== "none") {
        closeSearch()
      }
    },
    [buildResultContext, closeSearch],
  )

  const handleInputKeyDown = (event: React.KeyboardEvent<HTMLInputElement>) => {
    if (event.key === "ArrowDown") {
      event.preventDefault()
      if (flatItems.length === 0) return
      setActiveIndex((current) => (current + 1) % flatItems.length)
      return
    }

    if (event.key === "ArrowUp") {
      event.preventDefault()
      if (flatItems.length === 0) return
      setActiveIndex((current) => (current - 1 + flatItems.length) % flatItems.length)
      return
    }

    if (event.key === "Enter") {
      if (flatItems.length === 0) return
      event.preventDefault()
      void handleSelect(flatItems[activeIndex] ?? flatItems[0])
    }
  }

  const handleAskAssistant = React.useCallback(async () => {
    const handoff = agentHandoff
    const targetAgentName = handoff?.agentName ?? handoff?.assistantName
    if (!handoff || !targetAgentName) return

    const context: LemmaGlobalSearchAgentContext = {
      query: debouncedQuery,
      results: assistantResults.slice(0, handoff.resultLimit ?? 8),
      close: closeSearch,
    }

    setAssistantState({ status: "loading", message: null })

    try {
      const title =
        typeof handoff.conversationTitle === "function"
          ? handoff.conversationTitle(context)
          : handoff.conversationTitle ?? `Search: ${debouncedQuery.slice(0, 72)}`
      const message =
        handoff.buildMessage?.(context) ??
        buildDefaultAssistantMessage(context, handoff.include ?? "query-and-results")
      const conversation = await scopedClient.conversations.createForAgent(targetAgentName, { title })

      await scopedClient.conversations.messages.send(conversation.id, { content: message })
      await handoff.onConversationCreated?.(conversation, context)

      const href = handoff.href?.(conversation, context)
      if (href) {
        closeSearch()
        window.location.assign(href)
        return
      }

      setAssistantState({
        status: "success",
        message: `Sent to ${handoff.label ?? targetAgentName}.`,
      })
    } catch (assistantError) {
      const error = toError(assistantError, "Agent handoff failed.")
      handoff.onError?.(error)
      setAssistantState({ status: "error", message: error.message })
    }
  }, [agentHandoff, assistantResults, closeSearch, debouncedQuery, scopedClient])

  const sourceProgressStates = sourceStates.filter((source) => source.status === "loading" || source.status === "error")
  const firstSourceError = sourceStates.find((source) => source.status === "error")?.error

  return (
    <>
      <Button
        type="button"
        variant={triggerVariant(appearance)}
        size="sm"
        className={cn(triggerClassName(appearance, radius), className)}
        onClick={() => setOpen(true)}
        disabled={!enabled}
      >
        <span className="flex min-w-0 items-center gap-2 truncate text-muted-foreground">
          <Search data-icon="inline-start" />
          <span className="truncate">{triggerLabel}</span>
        </span>
        <span className={cn("border border-border/70 bg-muted/60 px-1.5 py-0.5 text-[11px] text-muted-foreground", searchRadiusClassName(radius, "control"))}>
          {shortcutLabel}
        </span>
      </Button>

      <Dialog open={open} onOpenChange={handleOpenChange}>
        <DialogContent showCloseButton={false} className={cn(dialogClassNameFor(appearance, radius), dialogClassName)}>
          <div className="sr-only">
            <DialogTitle>Global search</DialogTitle>
            <DialogDescription>Search across records and files.</DialogDescription>
          </div>

          <div
            className={cn(
              "grid grid-cols-[auto_minmax(0,1fr)_auto] items-center gap-3 px-5 py-4",
              appearance === "borderless" || appearance === "minimal" ? "border-b border-border/15" : "border-b border-border/60",
            )}
          >
            <Search className="size-4 text-muted-foreground" />
            <Input
              ref={inputRef}
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              onKeyDown={handleInputKeyDown}
              placeholder={placeholder}
              className="h-9 border-0 bg-transparent px-0 text-base shadow-none focus-visible:ring-0 md:text-base"
            />
            <Button type="button" variant="ghost" size="icon-sm" className="shrink-0" onClick={closeSearch}>
              <X />
              <span className="sr-only">Close search</span>
            </Button>
          </div>

          <div className="max-h-[68vh] overflow-y-auto">
            {!trimmedQuery ? (
              <EmptyPanel
                appearance={appearance}
                radius={radius}
                title="Start typing to search"
                description={`Search starts after ${minQueryLength} characters and streams results source by source.`}
              />
            ) : null}

            {showTypeHint ? (
              <EmptyPanel
                appearance={appearance}
                radius={radius}
                title="Keep typing to search"
                description={`Enter at least ${minQueryLength} characters to search across your configured tables and files.`}
              />
            ) : null}

            {!showTypeHint && trimmedQuery && !hasResults && isLoading ? (
              <SearchLoadingPanel
                appearance={appearance}
                radius={radius}
                states={sourceStates}
              />
            ) : null}

            {!showTypeHint && !isLoading && hasSourceErrors && !hasResults ? (
              <EmptyPanel
                appearance={appearance}
                radius={radius}
                icon={AlertCircle}
                title="Search unavailable"
                description={firstSourceError ?? "One or more search sources failed."}
              />
            ) : null}

            {!showTypeHint && !isLoading && !hasSourceErrors && hasValidQuery && !hasResults ? (
              <EmptyPanel
                appearance={appearance}
                radius={radius}
                title="No results"
                description="Try a broader name, email, status, or filename."
              />
            ) : null}

            {hasResults ? (
              <div className="flex flex-col">
                {resultGroups.map((group, groupIndex) => (
                  <div key={group.key}>
                    <div className="flex items-center justify-between px-4 py-3">
                      <div className="flex items-center gap-2 text-[11px] font-medium tracking-[0.18em] text-muted-foreground uppercase">
                        {group.type === "files" ? (
                          <FileText className="size-3.5" />
                        ) : (
                          <Database className="size-3.5" />
                        )}
                        <span>{group.label}</span>
                      </div>
                      <Badge variant="secondary">{group.count}</Badge>
                    </div>

                    <div className="px-2 pb-3">
                      {group.items.map((item) => {
                        const itemIndex = flatItems.findIndex((candidate) => candidate.key === item.key)
                        const Icon = item.icon
                        const isActive = itemIndex === activeIndex

                        return (
                          <Button
                            key={item.key}
                            type="button"
                            variant={isActive ? "secondary" : "ghost"}
                            className={cn(resultRowClassName(density), "mb-1 h-auto w-full justify-start text-left", searchRadiusClassName(radius, "surface"))}
                            onMouseEnter={() => setActiveIndex(itemIndex)}
                            onClick={() => void handleSelect(item)}
                          >
                            <span className="flex w-full items-start gap-3">
                              <span className={cn(iconShellClassName(appearance), "flex size-9 shrink-0 items-center justify-center", searchRadiusClassName(radius, "control"))}>
                                <Icon className="size-4 text-muted-foreground" />
                              </span>
                              <span className="min-w-0 flex-1">
                                <span className="flex items-center gap-2">
                                  <span className="truncate font-medium">{item.title}</span>
                                  <Badge variant="outline">{item.type === "file" ? "file" : "record"}</Badge>
                                </span>
                                {item.subtitle ? (
                                  <span className="mt-1 block truncate text-xs text-muted-foreground">
                                    {item.subtitle}
                                  </span>
                                ) : null}
                                {item.preview ? (
                                  <span className="mt-1 block line-clamp-2 text-xs text-muted-foreground">
                                    {item.preview}
                                  </span>
                                ) : null}
                              </span>
                              {item.resolveHref || item.runSelect ? (
                                <ArrowUpRight className="mt-0.5 size-4 shrink-0 text-muted-foreground" />
                              ) : null}
                            </span>
                          </Button>
                        )
                      })}
                    </div>

                    {groupIndex < resultGroups.length - 1 ? <Separator /> : null}
                  </div>
                ))}
              </div>
            ) : null}

            {sourceProgressStates.length > 0 ? (
              <SourceStatusRows states={sourceProgressStates} appearance={appearance} radius={radius} />
            ) : null}
          </div>

          {assistantEnabled && hasValidQuery ? (
            <div className={cn("flex items-center justify-between gap-3 px-4 py-3", appearance === "borderless" ? "border-t-0" : "border-t border-border/60")}>
              <div className="min-w-0">
                <p className="truncate text-sm font-medium">Continue with agent</p>
                <p className="truncate text-xs text-muted-foreground">
                  Send the query{agentHandoff?.include === "query" ? "" : " and visible results"} to {agentHandoff?.label ?? agentHandoff?.agentName ?? agentHandoff?.assistantName}.
                </p>
              </div>
              <div className="flex shrink-0 items-center gap-2">
                {assistantState.message ? (
                  <span
                    className={cn(
                      "hidden max-w-48 truncate text-xs md:inline",
                      assistantState.status === "error" ? "text-destructive" : "text-muted-foreground",
                    )}
                  >
                    {assistantState.message}
                  </span>
                ) : null}
                <Button
                  type="button"
                  size="sm"
                  variant="secondary"
                  onClick={() => void handleAskAssistant()}
                  disabled={assistantState.status === "loading"}
                >
                  {assistantState.status === "loading" ? (
                    <Loader2 data-icon="inline-start" className="animate-spin" />
                  ) : (
                    <Bot data-icon="inline-start" />
                  )}
                  {agentHandoff?.label ?? "Ask agent"}
                </Button>
              </div>
            </div>
          ) : null}
        </DialogContent>
      </Dialog>
    </>
  )
}

function SourceStatusRows({
  states,
  appearance,
  radius,
}: {
  states: SearchSourceState[]
  appearance: LemmaGlobalSearchAppearance
  radius: LemmaGlobalSearchRadius
}) {
  return (
    <div className={cn("flex flex-col gap-1 px-4 py-3", appearance === "borderless" ? "border-t-0" : "border-t border-border/60")}>
      {states.map((source) => {
        const Icon = source.type === "files" ? FileText : Database
        const isLoading = source.status === "loading"
        const isError = source.status === "error"

        return (
          <div key={source.key} className={cn("flex items-center justify-between gap-3 px-2 py-1.5 text-xs text-muted-foreground", searchRadiusClassName(radius, "control"))}>
            <span className="flex min-w-0 items-center gap-2">
              {isLoading ? (
                <Loader2 className="size-3.5 shrink-0 animate-spin" />
              ) : isError ? (
                <AlertCircle className="size-3.5 shrink-0 text-destructive" />
              ) : (
                <Icon className="size-3.5 shrink-0" />
              )}
              <span className="truncate">{source.label}</span>
            </span>
            <span className={cn("shrink-0", isError ? "text-destructive" : null)}>
              {sourceStatusLabel(source)}
            </span>
          </div>
        )
      })}
    </div>
  )
}

function EmptyPanel({
  title,
  description,
  icon: Icon = Search,
  iconClassName,
  appearance,
  radius,
}: {
  title: string
  description: string
  icon?: ResultIcon
  iconClassName?: string
  appearance: LemmaGlobalSearchAppearance
  radius: LemmaGlobalSearchRadius
}) {
  return (
    <div className="flex flex-col items-center justify-center gap-3 px-6 py-14 text-center">
      <div className={cn(iconShellClassName(appearance), "flex size-11 items-center justify-center", searchRadiusClassName(radius, "pill"))}>
        <Icon className={cn("size-4 text-muted-foreground", iconClassName)} />
      </div>
      <div className="flex flex-col gap-1">
        <p className="font-medium text-foreground">{title}</p>
        <p className="max-w-md text-sm text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}

function SearchLoadingPanel({
  appearance,
  radius,
  states,
}: {
  appearance: LemmaGlobalSearchAppearance
  radius: LemmaGlobalSearchRadius
  states: SearchSourceState[]
}) {
  const activeSource = states.find((source) => source.status === "loading")

  return (
    <div className="flex flex-col gap-4 px-6 py-12">
      <div className="mx-auto flex max-w-md flex-col items-center gap-3 text-center">
        <div className={cn(iconShellClassName(appearance), "flex size-11 items-center justify-center", searchRadiusClassName(radius, "pill"))}>
          <Loader2 className="size-4 animate-spin text-muted-foreground" />
        </div>
        <div className="flex flex-col gap-1">
          <p className="font-medium text-foreground">
            {activeSource ? `Searching ${activeSource.label}` : "Searching workspace"}
          </p>
          <p className="text-sm text-muted-foreground">
            Results appear as soon as each source responds.
          </p>
        </div>
      </div>
      <div className="mx-auto grid w-full max-w-xl gap-2">
        {[0, 1, 2].map((item) => (
          <div key={item} className={cn("overflow-hidden bg-muted/35", searchRadiusClassName(radius, "surface"))}>
            <div
              className={cn(
                "h-10 animate-pulse bg-muted",
                searchRadiusClassName(radius, "surface"),
                item === 0 ? "w-11/12" : item === 1 ? "w-2/3" : "w-5/6",
              )}
            />
          </div>
        ))}
      </div>
    </div>
  )
}

async function searchTableSource(client: LemmaClient, table: LemmaGlobalSearchTable, query: string): Promise<SearchGroup> {
  const responses = await Promise.allSettled(
    table.searchFields.map((field) =>
      client.records.list(table.tableName, {
        limit: table.limit ?? 6,
        filters: [{ field, op: "ilike", value: `%${query}%` }],
      }),
    ),
  )

  const seen = new Map<string, SearchRecord>()

  for (const response of responses) {
    if (response.status !== "fulfilled") continue
    for (const record of (response.value.items ?? []) as SearchRecord[]) {
      const recordId = String(record.id ?? record[table.displayField ?? "id"] ?? "")
      if (!recordId || seen.has(recordId)) continue
      seen.set(recordId, record)
    }
  }

  const items = Array.from(seen.values())
    .sort((left, right) => scoreRecord(right, table, query) - scoreRecord(left, table, query))
    .slice(0, table.limit ?? 6)
    .map((record) => buildRecordItem(record, table, query))

  return {
    key: `table:${table.tableName}`,
    label: table.label,
    type: "table",
    count: items.length,
    items,
  }
}

function buildRecordItem(record: SearchRecord, table: LemmaGlobalSearchTable, query: string): SearchItem {
  const title =
    formatValue(record[table.displayField ?? table.searchFields[0]])
    ?? formatValue(record.id)
    ?? table.label
  const subtitle =
    formatValue(record[table.subtitleField ?? ""])
    ?? firstSecondaryValue(record, [table.subtitleField, ...table.searchFields], title)
  const preview = firstMatchingPreview(record, table, query, [title, subtitle].filter(Boolean) as string[])

  return {
    key: `record:${table.tableName}:${String(record.id ?? title)}`,
    type: "record",
    sourceLabel: table.label,
    title,
    subtitle,
    preview,
    icon: table.icon ?? Database,
    openMode: table.openMode,
    resolveHref: table.href ? (context) => table.href?.(record, context) : undefined,
    runSelect: table.onSelect ? (context) => table.onSelect?.(record, context) : undefined,
  }
}

function buildFileItem(result: FileSearchResult, config?: LemmaGlobalSearchFiles): SearchItem {
  const title = fileTitle(result)
  const subtitle = result.path || null
  const preview = normalizeSnippet(result.content)

  return {
    key: `file:${result.file_id}:${result.chunk_index}`,
    type: "file",
    sourceLabel: config?.label ?? "Files",
    title,
    subtitle,
    preview,
    icon: FileText,
    openMode: config?.openMode,
    resolveHref: config?.href ? (context) => config.href?.(result, context) : undefined,
    runSelect: config?.onSelect ? (context) => config.onSelect?.(result, context) : undefined,
  }
}

function buildDefaultAssistantMessage(
  context: LemmaGlobalSearchAgentContext,
  include: LemmaGlobalSearchAgent["include"],
) {
  if (include === "query") {
    return `Search query: ${context.query}\n\nHelp me continue from this search.`
  }

  const resultLines = context.results.length
    ? context.results.map((result) => {
        const parts = [`[${result.sourceLabel}]`, result.title]
        if (result.subtitle) parts.push(`- ${result.subtitle}`)
        if (result.preview) parts.push(`- ${result.preview}`)
        return `- ${parts.join(" ")}`
      })
    : ["- No matching records returned yet."]

  return [
    `Search query: ${context.query}`,
    "",
    "Visible search results:",
    ...resultLines,
    "",
    "Use the query and results as context, then help me decide the next useful action.",
  ].join("\n")
}

function summarizeItem(item: SearchItem): LemmaGlobalSearchResultSummary {
  return {
    key: item.key,
    type: item.type,
    sourceLabel: item.sourceLabel,
    title: item.title,
    subtitle: item.subtitle,
    preview: item.preview,
  }
}

function upsertGroup(groups: SearchGroup[], group: SearchGroup) {
  const withoutGroup = groups.filter((current) => current.key !== group.key)
  if (group.count === 0) return withoutGroup
  return [...withoutGroup, group]
}

function updateSourceState(
  states: SearchSourceState[],
  key: string,
  patch: Partial<Pick<SearchSourceState, "status" | "count" | "error">>,
) {
  return states.map((state) => (state.key === key ? { ...state, ...patch } : state))
}

function sourceStatusLabel(source: SearchSourceState) {
  if (source.status === "loading") return "Searching"
  if (source.status === "error") return source.error ?? "Failed"
  if (source.status === "empty") return "No matches"
  return `${source.count} result${source.count === 1 ? "" : "s"}`
}

function fileTitle(result: FileSearchResult) {
  const metadataTitle = typeof result.metadata?.title === "string" ? result.metadata.title : null
  if (metadataTitle) return metadataTitle
  const parts = result.path.split("/")
  return parts[parts.length - 1] || result.path || "File"
}

function scoreRecord(record: SearchRecord, table: LemmaGlobalSearchTable, query: string) {
  const normalizedQuery = query.toLowerCase()
  const fields = [table.displayField, table.subtitleField, ...table.searchFields].filter(Boolean) as string[]
  let bestScore = 0

  for (const field of fields) {
    const value = normalizeSearchText(record[field])
    if (!value) continue
    if (value === normalizedQuery) bestScore = Math.max(bestScore, 400)
    else if (value.startsWith(normalizedQuery)) bestScore = Math.max(bestScore, 300)
    else if (value.includes(` ${normalizedQuery}`)) bestScore = Math.max(bestScore, 240)
    else if (value.includes(normalizedQuery)) bestScore = Math.max(bestScore, 180)
  }

  return bestScore
}

function firstMatchingPreview(
  record: SearchRecord,
  table: LemmaGlobalSearchTable,
  query: string,
  exclusions: string[],
) {
  const normalizedQuery = query.toLowerCase()
  const excluded = new Set(exclusions.map((entry) => entry.toLowerCase()))

  for (const field of table.searchFields) {
    const raw = formatValue(record[field])
    if (!raw) continue
    const normalized = raw.toLowerCase()
    if (excluded.has(normalized)) continue
    if (normalized.includes(normalizedQuery)) return raw
  }

  return null
}

function firstSecondaryValue(record: SearchRecord, fields: Array<string | undefined>, title: string) {
  const normalizedTitle = title.toLowerCase()
  for (const field of fields) {
    if (!field) continue
    const value = formatValue(record[field])
    if (!value || value.toLowerCase() === normalizedTitle) continue
    return value
  }
  return null
}

function formatValue(value: unknown): string | null {
  if (value == null) return null
  if (typeof value === "number" || typeof value === "boolean") return String(value)
  if (typeof value === "string") {
    const trimmed = value.trim()
    if (!trimmed) return null
    if (isUuidLike(trimmed)) return shortenIdentifier(trimmed)
    return trimmed
  }
  if (value instanceof Date) return value.toLocaleDateString()
  if (Array.isArray(value)) {
    const parts = value
      .map((entry) => formatValue(entry))
      .filter((entry): entry is string => Boolean(entry))
    return parts.join(", ") || null
  }
  return null
}

function normalizeSearchText(value: unknown) {
  return formatValue(value)?.toLowerCase() ?? ""
}

function normalizeSnippet(value: string | null | undefined) {
  if (!value) return null
  return value.replace(/\s+/g, " ").trim().slice(0, 180) || null
}

function normalizeMessage(error: unknown, fallback: string) {
  return toError(error, fallback).message
}

function toError(error: unknown, fallback: string) {
  if (error instanceof Error && error.message.trim()) return error
  if (typeof error === "string" && error.trim()) return new Error(error)
  return new Error(fallback)
}

function isUuidLike(value: string) {
  return /^[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}$/i.test(value)
}

function shortenIdentifier(value: string) {
  if (value.length <= 14) return value
  return `${value.slice(0, 8)}...${value.slice(-4)}`
}

function useDebouncedValue<T>(value: T, delayMs: number) {
  const [debounced, setDebounced] = React.useState(value)

  React.useEffect(() => {
    const timer = window.setTimeout(() => setDebounced(value), delayMs)
    return () => window.clearTimeout(timer)
  }, [delayMs, value])

  return debounced
}

function triggerVariant(appearance: LemmaGlobalSearchAppearance) {
  return appearance === "minimal" || appearance === "borderless" ? "ghost" : "outline"
}

function triggerClassName(appearance: LemmaGlobalSearchAppearance, radius: LemmaGlobalSearchRadius) {
  return cn(
    "w-full justify-between gap-3 md:w-[22rem]",
    searchRadiusClassName(radius, "control"),
    appearance === "minimal" ? "border-transparent bg-transparent shadow-none hover:bg-muted/40" : null,
    appearance === "borderless" ? "border-transparent bg-transparent shadow-none" : null,
  )
}

function dialogClassNameFor(appearance: LemmaGlobalSearchAppearance, radius: LemmaGlobalSearchRadius) {
  return cn(
    "max-w-4xl gap-0 overflow-hidden p-0 sm:max-w-3xl",
    searchRadiusClassName(radius, "overlay"),
    appearance === "minimal" ? "border-transparent bg-background/95 shadow-none ring-1 ring-border/15" : null,
    appearance === "borderless" ? "border-transparent shadow-2xl ring-0" : null,
    appearance === "contained" ? "border-border/80 bg-card shadow-xl" : null,
  )
}

function resultRowClassName(density: LemmaGlobalSearchDensity) {
  if (density === "compact") return "px-2 py-2"
  if (density === "spacious") return "px-4 py-4"
  return "px-3 py-3"
}

function iconShellClassName(appearance: LemmaGlobalSearchAppearance) {
  return cn(
    "bg-muted/50",
    appearance === "borderless" ? "border-0" : "border border-border/70",
    appearance === "minimal" ? "border-transparent bg-muted/25" : null,
    appearance === "contained" ? "bg-background" : null,
  )
}

function searchRadiusClassName(
  radius: LemmaGlobalSearchRadius = "lg",
  target: "surface" | "control" | "pill" | "overlay" = "surface",
) {
  if (radius === "none") return "rounded-none"
  if (radius === "sm") return target === "surface" || target === "overlay" ? "rounded-md" : "rounded-sm"
  if (radius === "md") return target === "surface" || target === "overlay" ? "rounded-lg" : "rounded-md"
  if (radius === "xl") return target === "pill" ? "rounded-full" : target === "control" ? "rounded-xl" : "rounded-2xl"
  return target === "pill" ? "rounded-full" : target === "control" ? "rounded-lg" : "rounded-xl"
}
