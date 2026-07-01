import * as React from "react"
import {
  Download,
  File,
  FileText,
  Folder,
  FolderPlus,
  Building2,
  Home,
  Image,
  Loader2,
  LockKeyhole,
  MoreHorizontal,
  Pencil,
  RefreshCw,
  Search,
  Trash2,
  Upload,
  Waypoints,
} from "lucide-react"
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
} from "@/components/lemma/ui/alert-dialog"
import { Button } from "@/components/lemma/ui/button"
import {
  Breadcrumb,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/lemma/ui/breadcrumb"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/lemma/ui/dialog"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/lemma/ui/dropdown-menu"
import { Input } from "@/components/lemma/ui/input"
import { Label } from "@/components/lemma/ui/label"
import { Skeleton } from "@/components/lemma/ui/skeleton"
import { useFiles, useFileSearch } from "lemma-sdk/react"
import type { FileResponse, FileSearchResultSchema, LemmaClient } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"

export type LemmaFileBrowserAppearance = "default" | "minimal" | "borderless" | "contained"
export type LemmaFileBrowserDensity = "compact" | "comfortable" | "spacious"
export type LemmaFileBrowserRadius = "none" | "sm" | "md" | "lg" | "xl"
export type LemmaFileBrowserMode = "browser" | "picker"
export type LemmaFileBrowserNamespace = "PRIVATE" | "POD"

export interface LemmaFileBrowserLinkAction {
  key: string
  label: React.ReactNode
  onSelect: (file: FileResponse) => void | Promise<void>
  disabled?: boolean
  hidden?: boolean
}

export interface LemmaFileBrowserProps {
  client: LemmaClient
  podId?: string
  initialPath?: string
  selectedPath?: string | null
  enabled?: boolean
  limit?: number
  searchMinLength?: number
  mode?: LemmaFileBrowserMode
  namespace?: LemmaFileBrowserNamespace
  showNamespaceTabs?: boolean
  pickLabel?: React.ReactNode
  uploadEnabled?: boolean
  deleteEnabled?: boolean
  renameEnabled?: boolean
  moveEnabled?: boolean
  publishToPodEnabled?: boolean
  createFolderEnabled?: boolean
  linkActions?: LemmaFileBrowserLinkAction[]
  appearance?: LemmaFileBrowserAppearance
  density?: LemmaFileBrowserDensity
  radius?: LemmaFileBrowserRadius
  title?: React.ReactNode
  headerActions?: React.ReactNode
  className?: string
  onFileOpen?: (file: FileResponse) => void
  onSearchResultOpen?: (result: FileSearchResultSchema) => void
  onPick?: (file: FileResponse) => void | Promise<void>
  onPathChange?: (path: string) => void
  onUploadSuccess?: (file: FileResponse) => void
  onDeleteSuccess?: (file: FileResponse) => void
  onFolderCreateSuccess?: (folder: FileResponse) => void
  onMutationSuccess?: (file: FileResponse, context: { kind: "rename" | "move" | "publish-to-pod"; previousPath: string }) => void
}

type FileBrowserEntry =
  | {
      key: string
      title: string
      path: string
      kind: string
      size?: number
      status?: string
      updatedAt?: string
      raw: FileResponse
      source: "browse"
      namespace: LemmaFileBrowserNamespace
    }
  | {
      key: string
      title: string
      path: string
      kind: "file"
      preview?: string
      raw: FileSearchResultSchema
      source: "search"
      namespace: LemmaFileBrowserNamespace
    }

export function LemmaFileBrowser({
  client,
  podId,
  initialPath = "/",
  selectedPath = null,
  enabled = true,
  limit = 100,
  searchMinLength = 2,
  mode = "browser",
  namespace,
  showNamespaceTabs = true,
  pickLabel = "Select",
  uploadEnabled,
  deleteEnabled,
  renameEnabled,
  moveEnabled,
  publishToPodEnabled,
  createFolderEnabled,
  linkActions = [],
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  title,
  headerActions,
  className,
  onFileOpen,
  onSearchResultOpen,
  onPick,
  onPathChange,
  onUploadSuccess,
  onDeleteSuccess,
  onFolderCreateSuccess,
  onMutationSuccess,
}: LemmaFileBrowserProps) {
  const [currentPath, setCurrentPath] = React.useState(normalizePath(initialPath))
  const [activeNamespace, setActiveNamespace] = React.useState<LemmaFileBrowserNamespace>(
    namespace ?? (mode === "picker" ? "PRIVATE" : "POD"),
  )
  const [query, setQuery] = React.useState("")
  const [uploading, setUploading] = React.useState(false)
  const [deleteTarget, setDeleteTarget] = React.useState<FileResponse | null>(null)
  const [deleting, setDeleting] = React.useState(false)
  const [createFolderOpen, setCreateFolderOpen] = React.useState(false)
  const [newFolderName, setNewFolderName] = React.useState("")
  const [creatingFolder, setCreatingFolder] = React.useState(false)
  const [renameTarget, setRenameTarget] = React.useState<FileResponse | null>(null)
  const [renameValue, setRenameValue] = React.useState("")
  const [renaming, setRenaming] = React.useState(false)
  const [moveTarget, setMoveTarget] = React.useState<FileResponse | null>(null)
  const [moveDestination, setMoveDestination] = React.useState("/")
  const [moving, setMoving] = React.useState(false)
  const [publishingPath, setPublishingPath] = React.useState<string | null>(null)
  const [mutationError, setMutationError] = React.useState<string | null>(null)
  const [pendingPickPath, setPendingPickPath] = React.useState<string | null>(null)
  const [pendingLinkKey, setPendingLinkKey] = React.useState<string | null>(null)
  const fileInputRef = React.useRef<HTMLInputElement | null>(null)
  const scopedClient = React.useMemo(() => (podId ? client.withPod(podId) : client), [client, podId])
  const trimmedQuery = query.trim()
  const hasSearch = trimmedQuery.length >= searchMinLength
  const normalizedSelectedPath = normalizeOptionalPath(selectedPath)
  const namespaceTabsVisible = showNamespaceTabs && !hasSearch

  const filesState = useFiles({
    client,
    podId,
    enabled,
    directoryPath: currentPath,
    namespace: activeNamespace,
    limit,
  })

  const searchState = useFileSearch({
    client,
    podId,
    enabled: enabled && hasSearch,
    query: trimmedQuery,
    minQueryLength: searchMinLength,
    limit,
  })

  React.useEffect(() => {
    const nextPath = normalizePath(initialPath)
    setCurrentPath(nextPath)
  }, [initialPath])

  React.useEffect(() => {
    if (namespace) {
      setActiveNamespace(namespace)
    }
  }, [namespace])

  const entries = React.useMemo<FileBrowserEntry[]>(() => {
    if (hasSearch) {
      return searchState.results.map((result) => ({
        key: `search:${result.file_id}:${result.chunk_index}`,
        title: displayFileNameFromPath(result.path),
        path: result.path,
        kind: "file",
        preview: result.content,
        raw: result,
        source: "search",
        namespace: activeNamespace,
      }))
    }

    return filesState.files.map((file) => ({
      key: `${activeNamespace}:${file.id ?? file.path}`,
      title: displayFileName(file.name || fileNameFromPath(file.path)),
      path: file.path,
      kind: file.kind,
      size: file.size_bytes,
      status: file.status,
      updatedAt: file.updated_at,
      raw: file,
      source: "browse",
      namespace: activeNamespace,
    }))
  }, [activeNamespace, filesState.files, hasSearch, searchState.results])

  const isLoading = hasSearch ? searchState.isLoading : filesState.isLoading
  const error = hasSearch ? searchState.error : filesState.error

  const refreshVisibleState = React.useCallback(async () => {
    if (hasSearch) {
      await searchState.search({ query: trimmedQuery })
      return
    }
    await filesState.refresh({ namespace: activeNamespace })
  }, [activeNamespace, filesState, hasSearch, searchState, trimmedQuery])

  const switchNamespace = React.useCallback((nextNamespace: LemmaFileBrowserNamespace) => {
    if (nextNamespace === activeNamespace) return
    setActiveNamespace(nextNamespace)
    setQuery("")
    setMutationError(null)
  }, [activeNamespace])

  const navigateToPath = React.useCallback((path: string) => {
    const nextPath = normalizePath(path)
    setCurrentPath(nextPath)
    setQuery("")
    setMutationError(null)
    onPathChange?.(nextPath)
  }, [onPathChange])

  const resolveEntryFile = React.useCallback(async (entry: FileBrowserEntry) => {
    if (entry.source === "browse") return entry.raw
    return scopedClient.files.get(entry.path, { namespace: entry.namespace })
  }, [scopedClient])

  const handleEntryOpen = React.useCallback((entry: FileBrowserEntry) => {
    setMutationError(null)
    if (isDirectoryKind(entry.kind)) {
      navigateToPath(entry.path)
      return
    }
    if (entry.source === "search") {
      onSearchResultOpen?.(entry.raw)
      return
    }
    onFileOpen?.(entry.raw)
  }, [navigateToPath, onFileOpen, onSearchResultOpen])

  const handleUpload = React.useCallback(async (fileList: FileList | null) => {
    const selected = fileList ? Array.from(fileList) : []
    if (selected.length === 0) return
    setUploading(true)
    setMutationError(null)
    try {
      for (const file of selected) {
        const uploaded = await scopedClient.files.upload(file, { directoryPath: currentPath, namespace: activeNamespace })
        onUploadSuccess?.(uploaded)
      }
      await filesState.refresh({ namespace: activeNamespace })
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Failed to upload files.")
    } finally {
      setUploading(false)
      if (fileInputRef.current) fileInputRef.current.value = ""
    }
  }, [activeNamespace, currentPath, filesState, onUploadSuccess, scopedClient])

  const handleCreateFolder = React.useCallback(async () => {
    const trimmedName = newFolderName.trim()
    if (!trimmedName || creatingFolder) return
    setCreatingFolder(true)
    setMutationError(null)
    try {
      const folder = await scopedClient.files.folder.create(trimmedName, { directoryPath: currentPath, namespace: activeNamespace })
      onFolderCreateSuccess?.(folder)
      setCreateFolderOpen(false)
      setNewFolderName("")
      await filesState.refresh({ namespace: activeNamespace })
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Failed to create folder.")
    } finally {
      setCreatingFolder(false)
    }
  }, [activeNamespace, creatingFolder, currentPath, filesState, newFolderName, onFolderCreateSuccess, scopedClient])

  const handleDelete = React.useCallback(async () => {
    if (!deleteTarget) return
    setDeleting(true)
    setMutationError(null)
    try {
      await scopedClient.files.delete(deleteTarget.path, { namespace: activeNamespace })
      onDeleteSuccess?.(deleteTarget)
      setDeleteTarget(null)
      await filesState.refresh({ namespace: activeNamespace })
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Failed to delete file.")
    } finally {
      setDeleting(false)
    }
  }, [activeNamespace, deleteTarget, filesState, onDeleteSuccess, scopedClient])

  const handleRename = React.useCallback(async () => {
    const target = renameTarget
    const trimmedName = renameValue.trim()
    if (!target || !trimmedName || renaming) return
    setRenaming(true)
    setMutationError(null)
    try {
      const nextFile = await scopedClient.files.update(target.path, { name: normalizeMutationName(target.path, trimmedName), namespace: activeNamespace })
      const nextCurrentPath = rebaseNestedPath(currentPath, target.path, nextFile.path)
      if (nextCurrentPath !== currentPath) {
        setCurrentPath(nextCurrentPath)
        onPathChange?.(nextCurrentPath)
      }
      onMutationSuccess?.(nextFile, { kind: "rename", previousPath: target.path })
      setRenameTarget(null)
      setRenameValue("")
      await refreshVisibleState()
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Failed to rename item.")
    } finally {
      setRenaming(false)
    }
  }, [activeNamespace, currentPath, onMutationSuccess, onPathChange, refreshVisibleState, renameTarget, renameValue, renaming, scopedClient])

  const handleMove = React.useCallback(async () => {
    const target = moveTarget
    const destination = normalizePath(moveDestination)
    if (!target || moving) return
    setMoving(true)
    setMutationError(null)
    try {
      const nextFile = await scopedClient.files.update(target.path, { directoryPath: destination, namespace: activeNamespace })
      const nextCurrentPath = rebaseNestedPath(currentPath, target.path, nextFile.path)
      if (nextCurrentPath !== currentPath) {
        setCurrentPath(nextCurrentPath)
        onPathChange?.(nextCurrentPath)
      }
      onMutationSuccess?.(nextFile, { kind: "move", previousPath: target.path })
      setMoveTarget(null)
      setMoveDestination("/")
      await refreshVisibleState()
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Failed to move item.")
    } finally {
      setMoving(false)
    }
  }, [activeNamespace, currentPath, moveDestination, moveTarget, moving, onMutationSuccess, onPathChange, refreshVisibleState, scopedClient])

  const handlePublishToPod = React.useCallback(async (target: FileResponse) => {
    if (publishingPath) return
    setPublishingPath(target.path)
    setMutationError(null)
    try {
      const nextFile = await scopedClient.files.update(target.path, { namespace: "POD" })
      onMutationSuccess?.(nextFile, { kind: "publish-to-pod", previousPath: target.path })
      await refreshVisibleState()
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Failed to publish file to pod.")
    } finally {
      setPublishingPath(null)
    }
  }, [onMutationSuccess, publishingPath, refreshVisibleState, scopedClient])

  const handlePick = React.useCallback(async (entry: FileBrowserEntry) => {
    if (!onPick || pendingPickPath) return
    setPendingPickPath(entry.path)
    setMutationError(null)
    try {
      const file = await resolveEntryFile(entry)
      await onPick(file)
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Failed to select file.")
    } finally {
      setPendingPickPath(null)
    }
  }, [onPick, pendingPickPath, resolveEntryFile])

  const handleLinkAction = React.useCallback(async (entry: FileBrowserEntry, action: LemmaFileBrowserLinkAction) => {
    if (pendingLinkKey || action.disabled || action.hidden) return
    setPendingLinkKey(action.key)
    setMutationError(null)
    try {
      const file = await resolveEntryFile(entry)
      await action.onSelect(file)
    } catch (error) {
      setMutationError(error instanceof Error ? error.message : "Failed to link file.")
    } finally {
      setPendingLinkKey(null)
    }
  }, [pendingLinkKey, resolveEntryFile])

  const statusLine = hasSearch
    ? `${searchState.totalResults} search result${searchState.totalResults === 1 ? "" : "s"}`
    : `${entries.length} ${namespaceLabel(activeNamespace).toLowerCase()} item${entries.length === 1 ? "" : "s"} in ${currentPath}`

  return (
    <div
      data-appearance={appearance}
      data-density={density}
      data-radius={radius}
      className={cn("lemma-file-browser flex h-full min-h-0 flex-col", rootClassName(appearance), className)}
    >
      <div className={cn("shrink-0", headerClassName(appearance))}>
        <div className={cn("flex flex-col gap-3", toolbarClassName(density))}>
          <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 flex items-center gap-3">
              <span className={cn("flex size-8 items-center justify-center border border-border/50 bg-muted/40 text-muted-foreground", radiusClassName(radius, "control"))}>
                <Folder className="size-4" />
              </span>
              <div className="min-w-0">
                <h1 className="truncate text-sm font-semibold text-foreground">{title ?? "Files"}</h1>
                <p className="truncate text-xs text-muted-foreground">{statusLine}</p>
              </div>
            </div>
            <div className="flex shrink-0 flex-wrap items-center gap-2">
              {headerActions}
              <Button variant="ghost" size="icon-sm" onClick={() => void refreshVisibleState()} disabled={isLoading}>
                <RefreshCw className={cn(isLoading ? "animate-spin" : undefined)} />
              </Button>
              {createFolderEnabled && !hasSearch ? (
                <Button variant="outline" size="sm" onClick={() => { setCreateFolderOpen(true); setMutationError(null) }}>
                  <FolderPlus data-icon="inline-start" />
                  Folder
                </Button>
              ) : null}
              {uploadEnabled && !hasSearch ? (
                <>
                  <input
                    ref={fileInputRef}
                    type="file"
                    multiple
                    className="hidden"
                    onChange={(event) => void handleUpload(event.target.files)}
                  />
                  <Button size="sm" onClick={() => fileInputRef.current?.click()} disabled={uploading}>
                    {uploading ? <Loader2 data-icon="inline-start" className="animate-spin" /> : <Upload data-icon="inline-start" />}
                    Upload
                  </Button>
                </>
              ) : null}
            </div>
          </div>

          <div className="flex flex-col gap-2 md:flex-row md:items-center md:justify-between">
            <div className="flex min-w-0 flex-col gap-2 md:flex-row md:items-center">
              {namespaceTabsVisible ? (
                <div className={cn("inline-flex shrink-0 border border-border/50 bg-muted/20 p-0.5", radiusClassName(radius, "control"))}>
                  {(["PRIVATE", "POD"] as const).map((item) => (
                    <button
                      key={item}
                      type="button"
                      className={cn(
                        "inline-flex h-7 items-center gap-1.5 px-2.5 text-xs font-medium transition-colors",
                        radiusClassName(radius, "control"),
                        activeNamespace === item ? "bg-background text-foreground shadow-sm" : "text-muted-foreground hover:text-foreground",
                      )}
                      onClick={() => switchNamespace(item)}
                    >
                      {item === "PRIVATE" ? <LockKeyhole className="size-3.5" /> : <Building2 className="size-3.5" />}
                      {namespaceLabel(item)}
                    </button>
                  ))}
                </div>
              ) : null}
              <FilePathBreadcrumb path={currentPath} radius={radius} onNavigate={navigateToPath} />
            </div>
            <div className="relative w-full md:w-72">
              <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={mode === "picker" ? "Search and select files..." : "Search files..."}
                className={cn("h-8 pl-8 text-xs", radiusClassName(radius, "control"))}
              />
            </div>
          </div>

          {mode === "picker" || linkActions.length > 0 || mutationError ? (
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              {mode === "picker" ? <span>Picker mode is active: open for preview, or use the select action.</span> : null}
              {linkActions.length > 0 ? <span>Use row actions to link files into your own record or workflow flows.</span> : null}
              {mutationError ? <span className="text-destructive">{mutationError}</span> : null}
            </div>
          ) : null}
        </div>
      </div>

      <div className={cn("flex-1 overflow-auto", contentClassName(density))}>
        {error ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
            <p className="text-sm text-destructive">{error.message}</p>
            <Button variant="outline" size="sm" onClick={() => void refreshVisibleState()}>
              <RefreshCw data-icon="inline-start" />
              Retry
            </Button>
          </div>
        ) : isLoading ? (
          <div className="flex flex-col gap-2">
            {Array.from({ length: 6 }).map((_, index) => (
              <div key={index} className="flex items-center gap-3">
                <Skeleton className="size-10 shrink-0 rounded-md" />
                <div className="flex flex-1 flex-col gap-2">
                  <Skeleton className="h-4 w-2/3" />
                  <Skeleton className="h-3 w-1/3" />
                </div>
              </div>
            ))}
          </div>
        ) : entries.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
            <span className={cn("flex size-11 items-center justify-center border border-border/60 bg-muted/40 text-muted-foreground", radiusClassName(radius, "pill"))}>
              <Folder className="size-5" />
            </span>
            <div>
              <p className="font-medium text-foreground">{hasSearch ? "No matching files" : "No files here"}</p>
              <p className="mt-1 text-sm text-muted-foreground">{hasSearch ? "Try a broader filename or phrase." : "Upload files, create a folder, or open another directory."}</p>
            </div>
          </div>
        ) : (
          <div className={cn("flex flex-col", density === "compact" ? "gap-1" : "gap-2")}>
            {entries.map((entry) => {
              const isDirectory = isDirectoryKind(entry.kind)
              const extension = isDirectory ? "" : extensionFromPath(entry.path)
              const isSelected = normalizedSelectedPath === normalizePath(entry.path)
              const showPickButton = mode === "picker" && !isDirectory && Boolean(onPick)
              const canPublishToPod = !hasSearch && !isDirectory && activeNamespace === "PRIVATE" && publishToPodEnabled !== false
              const rowMenuVisible = (!hasSearch && (renameEnabled || moveEnabled || deleteEnabled || canPublishToPod)) || (!isDirectory && linkActions.some((action) => !action.hidden))
              return (
                <div
                  key={entry.key}
                  className={cn(
                    "group flex items-center gap-3 border transition-colors",
                    isSelected ? "border-primary/50 bg-primary/5" : "border-border/30 bg-muted/10 hover:bg-muted/25",
                    radiusClassName(radius, "surface"),
                    density === "compact" ? "p-2" : density === "spacious" ? "p-4" : "p-3",
                  )}
                >
                  <button type="button" className="flex min-w-0 flex-1 items-center gap-3 text-left" onClick={() => handleEntryOpen(entry)}>
                    <span className={cn("flex shrink-0 items-center justify-center border border-border/40 bg-muted/20 text-muted-foreground", radiusClassName(radius, "control"), density === "compact" ? "size-8" : "size-10")}>
                      {isDirectory ? <Folder className="size-4" /> : fileIcon(extension)}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate text-sm font-medium text-foreground">{entry.title}</span>
                      <span className="mt-0.5 flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
                        <span className="truncate">{displayPath(entry.path)}</span>
                        {"size" in entry && typeof entry.size === "number" ? <span>{formatFileSize(entry.size)}</span> : null}
                        {"status" in entry && entry.status ? <span>{entry.status}</span> : null}
                        {"updatedAt" in entry && entry.updatedAt ? <span>{formatShortDate(entry.updatedAt)}</span> : null}
                        {"preview" in entry && entry.preview ? <span className="line-clamp-1">{entry.preview}</span> : null}
                      </span>
                    </span>
                  </button>

                  <div className="flex shrink-0 items-center gap-1 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100">
                    {!isDirectory ? (
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        onClick={() => void downloadFile(scopedClient, entry.path, entry.namespace)}
                        aria-label="Download file"
                      >
                        <Download />
                      </Button>
                    ) : null}
                    {showPickButton ? (
                      <Button
                        variant="outline"
                        size="sm"
                        disabled={pendingPickPath === entry.path}
                        onClick={() => void handlePick(entry)}
                      >
                        {pendingPickPath === entry.path ? <Loader2 data-icon="inline-start" className="animate-spin" /> : null}
                        {pickLabel}
                      </Button>
                    ) : null}
                    {rowMenuVisible ? (
                      <DropdownMenu>
                        <DropdownMenuTrigger
                          className={cn(
                            "inline-flex size-8 items-center justify-center rounded-md border border-transparent text-muted-foreground transition-colors hover:bg-muted/50 hover:text-foreground focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring/60",
                            radiusClassName(radius, "control"),
                          )}
                        >
                          <MoreHorizontal />
                        </DropdownMenuTrigger>
                        <DropdownMenuContent align="end" className="w-52">
                          {!isDirectory && linkActions.filter((action) => !action.hidden).map((action) => (
                            <DropdownMenuItem
                              key={action.key}
                              disabled={action.disabled || pendingLinkKey === action.key}
                              onClick={() => void handleLinkAction(entry, action)}
                            >
                              {pendingLinkKey === action.key ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Waypoints className="mr-2 size-4" />}
                              {action.label}
                            </DropdownMenuItem>
                          ))}
                          {!isDirectory && linkActions.some((action) => !action.hidden) && !hasSearch && (renameEnabled || moveEnabled || deleteEnabled || canPublishToPod) ? <DropdownMenuSeparator /> : null}
                          {canPublishToPod ? (
                            <DropdownMenuItem
                              disabled={publishingPath === entry.path}
                              onClick={() => {
                                if (entry.source !== "browse") return
                                void handlePublishToPod(entry.raw)
                              }}
                            >
                              {publishingPath === entry.path ? <Loader2 className="mr-2 size-4 animate-spin" /> : <Building2 className="mr-2 size-4" />}
                              Publish to pod
                            </DropdownMenuItem>
                          ) : null}
                          {!hasSearch && renameEnabled ? (
                            <DropdownMenuItem
                              onClick={() => {
                                if (entry.source !== "browse") return
                                setRenameTarget(entry.raw)
                                setRenameValue(displayFileName(entry.raw.name || fileNameFromPath(entry.raw.path)))
                                setMutationError(null)
                              }}
                            >
                              <Pencil className="mr-2 size-4" />
                              Rename
                            </DropdownMenuItem>
                          ) : null}
                          {!hasSearch && moveEnabled ? (
                            <DropdownMenuItem
                              onClick={() => {
                                if (entry.source !== "browse") return
                                setMoveTarget(entry.raw)
                                setMoveDestination(directoryFromPath(entry.raw.path))
                                setMutationError(null)
                              }}
                            >
                              <Waypoints className="mr-2 size-4" />
                              Move
                            </DropdownMenuItem>
                          ) : null}
                          {!hasSearch && deleteEnabled ? (
                            <DropdownMenuItem
                              className="text-destructive focus:text-destructive"
                              onClick={() => {
                                if (entry.source !== "browse") return
                                setDeleteTarget(entry.raw)
                                setMutationError(null)
                              }}
                            >
                              <Trash2 className="mr-2 size-4" />
                              Delete
                            </DropdownMenuItem>
                          ) : null}
                        </DropdownMenuContent>
                      </DropdownMenu>
                    ) : null}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>

      <AlertDialog open={!!deleteTarget} onOpenChange={(open) => !open && setDeleteTarget(null)}>
        <AlertDialogContent>
          <AlertDialogHeader>
            <AlertDialogTitle>Delete item?</AlertDialogTitle>
            <AlertDialogDescription>
              This removes {deleteTarget ? displayFileName(deleteTarget.name || fileNameFromPath(deleteTarget.path)) : "this item"} from the datastore. This cannot be undone.
            </AlertDialogDescription>
          </AlertDialogHeader>
          <AlertDialogFooter>
            <AlertDialogCancel disabled={deleting}>Cancel</AlertDialogCancel>
            <AlertDialogAction disabled={deleting} onClick={() => void handleDelete()}>
              {deleting ? "Deleting..." : "Delete"}
            </AlertDialogAction>
          </AlertDialogFooter>
        </AlertDialogContent>
      </AlertDialog>

      <Dialog open={createFolderOpen} onOpenChange={setCreateFolderOpen}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Create folder</DialogTitle>
            <DialogDescription>Create a new folder inside {currentPath}.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="lemma-file-browser-folder-name">Folder name</Label>
            <Input
              id="lemma-file-browser-folder-name"
              value={newFolderName}
              onChange={(event) => setNewFolderName(event.target.value)}
              placeholder="Q2 research"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setCreateFolderOpen(false)} disabled={creatingFolder}>Cancel</Button>
            <Button onClick={() => void handleCreateFolder()} disabled={!newFolderName.trim() || creatingFolder}>
              {creatingFolder ? <Loader2 data-icon="inline-start" className="animate-spin" /> : <FolderPlus data-icon="inline-start" />}
              Create folder
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!renameTarget} onOpenChange={(open) => !open && setRenameTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Rename item</DialogTitle>
            <DialogDescription>Update the display name for {renameTarget ? displayFileName(renameTarget.name || fileNameFromPath(renameTarget.path)) : "this item"}.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="lemma-file-browser-rename-name">Name</Label>
            <Input
              id="lemma-file-browser-rename-name"
              value={renameValue}
              onChange={(event) => setRenameValue(event.target.value)}
              placeholder="notes.md"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setRenameTarget(null)} disabled={renaming}>Cancel</Button>
            <Button onClick={() => void handleRename()} disabled={!renameValue.trim() || renaming}>
              {renaming ? <Loader2 data-icon="inline-start" className="animate-spin" /> : <Pencil data-icon="inline-start" />}
              Save
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={!!moveTarget} onOpenChange={(open) => !open && setMoveTarget(null)}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Move item</DialogTitle>
            <DialogDescription>Move {moveTarget ? displayFileName(moveTarget.name || fileNameFromPath(moveTarget.path)) : "this item"} into another folder path.</DialogDescription>
          </DialogHeader>
          <div className="grid gap-2">
            <Label htmlFor="lemma-file-browser-move-path">Destination folder</Label>
            <Input
              id="lemma-file-browser-move-path"
              value={moveDestination}
              onChange={(event) => setMoveDestination(event.target.value)}
              placeholder="/projects/archive"
            />
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setMoveTarget(null)} disabled={moving}>Cancel</Button>
            <Button onClick={() => void handleMove()} disabled={moving}>
              {moving ? <Loader2 data-icon="inline-start" className="animate-spin" /> : <Waypoints data-icon="inline-start" />}
              Move
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}

function FilePathBreadcrumb({
  path,
  radius,
  onNavigate,
}: {
  path: string
  radius: LemmaFileBrowserRadius
  onNavigate: (path: string) => void
}) {
  const parts = path.split("/").filter(Boolean)
  let currentPath = ""

  return (
    <Breadcrumb className="min-w-0">
      <BreadcrumbList className="flex-nowrap">
        <BreadcrumbItem>
          {parts.length === 0 ? (
            <BreadcrumbPage className="inline-flex items-center gap-1.5">
              <Home className="size-3.5" />
              Files
            </BreadcrumbPage>
          ) : (
            <BreadcrumbLink className={cn("inline-flex items-center gap-1.5", radiusClassName(radius, "control"))} href="#" onClick={(event) => { event.preventDefault(); onNavigate("/") }}>
              <Home className="size-3.5" />
              Files
            </BreadcrumbLink>
          )}
        </BreadcrumbItem>
        {parts.map((part, index) => {
          currentPath += `/${part}`
          const itemPath = currentPath
          const isLast = index === parts.length - 1
          return (
            <React.Fragment key={itemPath}>
              <BreadcrumbSeparator />
              <BreadcrumbItem className="min-w-0">
                {isLast ? (
                  <BreadcrumbPage className="max-w-48 truncate">{displayFileName(part)}</BreadcrumbPage>
                ) : (
                  <BreadcrumbLink className="max-w-40 truncate" href="#" onClick={(event) => { event.preventDefault(); onNavigate(itemPath) }}>
                    {displayFileName(part)}
                  </BreadcrumbLink>
                )}
              </BreadcrumbItem>
            </React.Fragment>
          )
        })}
      </BreadcrumbList>
    </Breadcrumb>
  )
}

async function downloadFile(client: LemmaClient, path: string, namespace: LemmaFileBrowserNamespace) {
  const blob = await client.files.download(path, { namespace })
  const url = URL.createObjectURL(blob)
  const anchor = document.createElement("a")
  anchor.href = url
  anchor.download = downloadFileNameFromPath(path)
  anchor.click()
  URL.revokeObjectURL(url)
}

function normalizePath(path: string) {
  const trimmed = path.trim()
  if (!trimmed || trimmed === "/") return "/"
  return `/${trimmed.replace(/^\/+|\/+$/g, "")}`
}

function normalizeOptionalPath(path?: string | null) {
  if (typeof path !== "string" || path.trim().length === 0) return null
  return normalizePath(path)
}

function directoryFromPath(path: string) {
  const normalized = normalizePath(path)
  if (normalized === "/") return "/"
  const parts = normalized.split("/").filter(Boolean)
  if (parts.length <= 1) return "/"
  return `/${parts.slice(0, -1).join("/")}`
}

function rebaseNestedPath(path: string, previousBase: string, nextBase: string) {
  const normalizedPath = normalizePath(path)
  const normalizedPrevious = normalizePath(previousBase)
  const normalizedNext = normalizePath(nextBase)
  if (normalizedPath === normalizedPrevious) return normalizedNext
  if (!normalizedPath.startsWith(`${normalizedPrevious}/`)) return normalizedPath
  return `${normalizedNext}${normalizedPath.slice(normalizedPrevious.length)}`
}

function fileNameFromPath(path: string) {
  const parts = path.split("/").filter(Boolean)
  return parts[parts.length - 1] ?? path
}

function stripInternalDocumentExtension(name: string) {
  return name.toLowerCase().endsWith(".lemma-doc.json") ? name.slice(0, -".lemma-doc.json".length) : name
}

function displayFileName(name: string) {
  return stripInternalDocumentExtension(name)
}

function displayFileNameFromPath(path: string) {
  return displayFileName(fileNameFromPath(path))
}

function displayPath(path: string) {
  const parts = path.split("/")
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    if (!parts[index]) continue
    parts[index] = displayFileName(parts[index])
    break
  }
  return parts.join("/")
}

function downloadFileNameFromPath(path: string) {
  const name = fileNameFromPath(path)
  if (!name.toLowerCase().endsWith(".lemma-doc.json")) return name
  return `${stripInternalDocumentExtension(name)}.json`
}

function normalizeMutationName(path: string, name: string) {
  const trimmed = name.trim()
  const currentName = fileNameFromPath(path).toLowerCase()
  if (!currentName.endsWith(".lemma-doc.json")) return trimmed
  const base = stripInternalDocumentExtension(trimmed).replace(/\.json$/i, "").trim()
  return `${base || "untitled-document"}.lemma-doc.json`
}

function extensionFromPath(path: string) {
  const name = fileNameFromPath(path)
  const index = name.lastIndexOf(".")
  if (index < 0) return ""
  return name.slice(index + 1).toLowerCase()
}

function fileIcon(extension: string) {
  if (/^(jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff|avif)$/.test(extension)) return <Image className="size-4" />
  if (/^(md|mdx|txt|csv|log|json|jsonc|xml|yaml|yml|toml|ini|conf|env|doc|docx|pdf|rtf|xls|xlsx|ppt|pptx|js|mjs|cjs|jsx|ts|mts|cts|tsx|css|scss|sass|less|html|htm|py|rb|go|rs|java|kt|swift|c|h|cc|cpp|hpp|cs|php|sh|bash|zsh|fish|sql|graphql|gql)$/.test(extension)) {
    return <FileText className="size-4" />
  }
  return <File className="size-4" />
}

function isDirectoryKind(kind: unknown) {
  return String(kind).toLowerCase() === "directory" || String(kind).toLowerCase() === "folder"
}

function namespaceLabel(namespace: LemmaFileBrowserNamespace) {
  return namespace === "PRIVATE" ? "Private" : "Pod"
}

function formatFileSize(bytes: number) {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  if (bytes < 1024 * 1024 * 1024) return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
  return `${(bytes / (1024 * 1024 * 1024)).toFixed(1)} GB`
}

function formatShortDate(value: string) {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
  }).format(date)
}

function rootClassName(appearance: LemmaFileBrowserAppearance) {
  if (appearance === "contained") return "bg-card"
  if (appearance === "minimal" || appearance === "borderless") return "bg-transparent"
  return "bg-background"
}

function headerClassName(appearance: LemmaFileBrowserAppearance) {
  if (appearance === "borderless") return "bg-transparent"
  if (appearance === "minimal") return "border-b border-border/15 bg-transparent"
  if (appearance === "contained") return "border-b border-border/60 bg-card"
  return "border-b border-border/40 bg-card/95"
}

function toolbarClassName(density: LemmaFileBrowserDensity) {
  if (density === "compact") return "px-3 py-2"
  if (density === "spacious") return "px-5 py-4"
  return "px-4 py-3"
}

function contentClassName(density: LemmaFileBrowserDensity) {
  if (density === "compact") return "p-2"
  if (density === "spacious") return "p-5"
  return "p-4"
}

function radiusClassName(radius: LemmaFileBrowserRadius, target: "surface" | "control" | "pill") {
  if (radius === "none") return "rounded-none"
  if (radius === "sm") return target === "surface" ? "rounded-md" : "rounded-sm"
  if (radius === "md") return target === "surface" ? "rounded-lg" : "rounded-md"
  if (radius === "xl") return target === "pill" ? "rounded-full" : target === "control" ? "rounded-xl" : "rounded-2xl"
  return target === "pill" ? "rounded-full" : target === "control" ? "rounded-lg" : "rounded-xl"
}
