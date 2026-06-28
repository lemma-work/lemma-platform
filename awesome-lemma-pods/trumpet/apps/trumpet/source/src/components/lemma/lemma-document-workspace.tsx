import "prosekit/basic/style.css"
import "@prosekit/extensions/table/style.css"

import * as React from "react"
import { defineBasicExtension, type BasicExtension } from "prosekit/basic"
import {
  canUseRegexLookbehind,
  createEditor,
  union,
  type Editor,
  type NodeJSON,
} from "prosekit/core"
import { definePlaceholder } from "prosekit/extensions/placeholder"
import { defineReadonly } from "prosekit/extensions/readonly"
import { ProseKit, useDocChange, useEditor, useEditorDerivedValue } from "prosekit/react"
import {
  AutocompleteEmpty,
  AutocompleteItem,
  AutocompleteList,
  AutocompletePopover,
} from "prosekit/react/autocomplete"
import {
  TableHandleColumnRoot,
  TableHandleColumnTrigger,
  TableHandleDragPreview,
  TableHandleDropIndicator,
  TableHandlePopoverContent,
  TableHandlePopoverItem,
  TableHandleRoot,
  TableHandleRowRoot,
  TableHandleRowTrigger,
} from "prosekit/react/table-handle"
import {
  AlertCircle,
  Bold,
  CheckSquare,
  Code2,
  Columns3,
  Database,
  Download,
  ExternalLink,
  File,
  FileText,
  Hash,
  Heading1,
  Heading2,
  Image,
  Italic,
  Link2,
  List,
  ListOrdered,
  MessageSquare,
  Minus,
  PanelRightOpen,
  Pilcrow,
  Plus,
  Quote,
  Redo2,
  RefreshCw,
  Rows3,
  Save,
  Search,
  Table2,
  Trash2,
  Undo2,
  WandSparkles,
  X,
} from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from "@/components/lemma/ui/dialog"
import { Input } from "@/components/lemma/ui/input"
import { Textarea } from "@/components/lemma/ui/textarea"
import { cn } from "@/components/lemma/lib/utils"
import { useFile, useFiles, useFileTree, useTables } from "lemma-sdk/react"
import type { DatastoreDirectoryTreeNode, DatastoreFileNamespace, FileResponse, LemmaClient } from "lemma-sdk"

export type LemmaDocumentWorkspaceMode = "page" | "modal"
export type LemmaDocumentWorkspaceIntent = "create" | "edit" | "read"
export type LemmaDocumentWorkspaceSaveState = "saved" | "saving" | "dirty" | "error"
export type LemmaDocumentWorkspaceAppearance = "default" | "minimal" | "borderless" | "contained"
export type LemmaDocumentWorkspaceDensity = "compact" | "comfortable" | "spacious"
export type LemmaDocumentWorkspaceRadius = "none" | "sm" | "md" | "lg" | "xl"
export type LemmaDocumentWorkspaceFileFormat = "auto" | "structured" | "markdown" | "text" | "image" | "pdf" | "html" | "download"
export type LemmaDocumentNode = NodeJSON
export type LemmaRichDocumentEditor = Editor<BasicExtension>
type MaybePromise<T> = T | Promise<T>

export interface LemmaDocumentWorkspaceMetadataItem {
  label: React.ReactNode
  value: React.ReactNode
}

export interface LemmaDocumentWorkspaceReference {
  id: string
  title: React.ReactNode
  description?: React.ReactNode
  meta?: React.ReactNode
  href?: string
  onSelect?: () => void
}

export interface LemmaDocumentWorkspaceContextItem {
  label: React.ReactNode
  value?: React.ReactNode
}

export interface LemmaDocumentWorkspaceChange {
  json: LemmaDocumentNode
  html: string
  text: string
}

export interface LemmaDocumentWorkspaceAction {
  id: string
  label: React.ReactNode
  description?: React.ReactNode
  disabled?: boolean
  onSelect: (context: LemmaDocumentWorkspaceContext) => void
}

export interface LemmaDocumentWorkspaceFileBlock {
  title: string
  path: string
  description?: string
  meta?: string
  href?: string
}

export interface LemmaDocumentWorkspaceRecordsFilter {
  label?: string
  value: string
}

export interface LemmaDocumentWorkspaceRecordsBlock {
  table: string
  title?: string
  description?: string
  filters?: Array<string | LemmaDocumentWorkspaceRecordsFilter>
  columns?: string[]
  sorts?: string[]
}

export interface LemmaDocumentWorkspaceCommand {
  id: string
  label: React.ReactNode
  description?: React.ReactNode
  icon?: React.ReactNode
  keywords?: string[]
  disabled?: boolean
  run: (editor: LemmaRichDocumentEditor, context: LemmaDocumentWorkspaceContext) => void | Promise<void>
}

export interface LemmaDocumentWorkspaceContext {
  title: string
  summary: string
  text: string
  html: string
  json: LemmaDocumentNode
  intent: LemmaDocumentWorkspaceIntent
  pathLabel?: React.ReactNode
  file?: FileResponse | null
  path?: string
}

export interface LemmaDocumentWorkspaceFileOptions {
  path?: string
  file?: FileResponse | null
  namespace?: DatastoreFileNamespace
  format?: LemmaDocumentWorkspaceFileFormat
  enabled?: boolean
  defaultDirectoryPath?: string
  defaultFileName?: string
  searchEnabled?: boolean
  convertedArtifact?: string
  textEditable?: boolean
  showMetadata?: boolean
  showBreadcrumbs?: boolean
}

export interface LemmaDocumentWorkspaceProps {
  client?: LemmaClient
  podId?: string
  file?: LemmaDocumentWorkspaceFileOptions
  titleValue?: string
  defaultTitle?: string
  onTitleChange?: (value: string) => void
  summaryValue?: string
  defaultSummary?: string
  onSummaryChange?: (value: string) => void
  value?: LemmaDocumentNode | string | null
  defaultValue?: LemmaDocumentNode | string | null
  onChange?: (change: LemmaDocumentWorkspaceChange) => void
  mode?: LemmaDocumentWorkspaceMode
  intent?: LemmaDocumentWorkspaceIntent
  open?: boolean
  onOpenChange?: (open: boolean) => void
  saveState?: LemmaDocumentWorkspaceSaveState
  onSave?: () => void
  saveDisabled?: boolean
  placeholder?: string
  status?: React.ReactNode
  lastEditedLabel?: React.ReactNode
  pathLabel?: React.ReactNode
  metadata?: LemmaDocumentWorkspaceMetadataItem[]
  backlinks?: LemmaDocumentWorkspaceReference[]
  references?: LemmaDocumentWorkspaceReference[]
  assistantContext?: LemmaDocumentWorkspaceContextItem[]
  assistantActions?: LemmaDocumentWorkspaceAction[]
  commands?: LemmaDocumentWorkspaceCommand[]
  filePickerRootPath?: string
  onInsertFile?: (context: LemmaDocumentWorkspaceContext) => MaybePromise<LemmaDocumentWorkspaceFileBlock | void>
  onInsertRecords?: (context: LemmaDocumentWorkspaceContext) => MaybePromise<LemmaDocumentWorkspaceRecordsBlock | void>
  onAskAssistant?: (context: LemmaDocumentWorkspaceContext) => void
  onOpenComments?: (context: LemmaDocumentWorkspaceContext) => void
  onOpenActivity?: (context: LemmaDocumentWorkspaceContext) => void
  headerActions?: React.ReactNode
  className?: string
  appearance?: LemmaDocumentWorkspaceAppearance
  density?: LemmaDocumentWorkspaceDensity
  radius?: LemmaDocumentWorkspaceRadius
  onCreateSuccess?: (file: FileResponse, context: LemmaDocumentWorkspaceContext) => void
  onSaveSuccess?: (file: FileResponse, context: LemmaDocumentWorkspaceContext) => void
  onOpenExternal?: (file: FileResponse | null, path: string) => void
}

const slashRegex = canUseRegexLookbehind() ? /(?<!\S)\/(\S.*)?$/u : /\/(\S.*)?$/u

export function LemmaDocumentWorkspace({
  client,
  podId,
  file: fileOptions,
  titleValue,
  defaultTitle = "",
  onTitleChange,
  summaryValue,
  defaultSummary = "",
  onSummaryChange,
  value,
  defaultValue,
  onChange,
  mode = "page",
  intent = "edit",
  open,
  onOpenChange,
  saveState: controlledSaveState = "saved",
  onSave,
  saveDisabled = false,
  placeholder = "Write, or press / for blocks.",
  status,
  lastEditedLabel,
  pathLabel,
  metadata = [],
  backlinks = [],
  references = [],
  assistantContext = [],
  assistantActions = [],
  commands = [],
  filePickerRootPath = "/",
  onInsertFile,
  onInsertRecords,
  onAskAssistant,
  onOpenComments,
  onOpenActivity,
  headerActions,
  className,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  onCreateSuccess,
  onSaveSuccess,
  onOpenExternal,
}: LemmaDocumentWorkspaceProps) {
  const [uncontrolledTitle, setUncontrolledTitle] = React.useState(defaultTitle)
  const [uncontrolledSummary, setUncontrolledSummary] = React.useState(defaultSummary)
  const [inspectorOpen, setInspectorOpen] = React.useState(false)
  const [commandMenuOpen, setCommandMenuOpen] = React.useState(false)
  const [commandQuery, setCommandQuery] = React.useState("")
  const [toolbarVisible, setToolbarVisible] = React.useState(true)
  const [editorFocused, setEditorFocused] = React.useState(false)
  const [filePickerOpen, setFilePickerOpen] = React.useState(false)
  const [filePickerDirectory, setFilePickerDirectory] = React.useState(normalizePath(filePickerRootPath))
  const [selectedFilePath, setSelectedFilePath] = React.useState<string | null>(null)
  const [fileReferenceTitle, setFileReferenceTitle] = React.useState("")
  const [fileReferenceDescription, setFileReferenceDescription] = React.useState("")
  const [fileReferenceMeta, setFileReferenceMeta] = React.useState("")
  const [recordsPickerOpen, setRecordsPickerOpen] = React.useState(false)
  const [selectedTableName, setSelectedTableName] = React.useState("")
  const [recordsReferenceTitle, setRecordsReferenceTitle] = React.useState("")
  const [recordsReferenceDescription, setRecordsReferenceDescription] = React.useState("")
  const [recordsFiltersText, setRecordsFiltersText] = React.useState("")
  const [recordsSortsText, setRecordsSortsText] = React.useState("")
  const [selectedRecordColumns, setSelectedRecordColumns] = React.useState<string[]>([])
  const [localFile, setLocalFile] = React.useState<FileResponse | null>(null)
  const [fileContent, setFileContent] = React.useState<string | null>(null)
  const [loadedFileSnapshot, setLoadedFileSnapshot] = React.useState<{
    title: string
    summary: string
    contentKey: string
    text: string
    path: string
  } | null>(null)
  const [objectUrl, setObjectUrl] = React.useState<string | null>(null)
  const [fileError, setFileError] = React.useState<Error | null>(null)
  const [isLoadingFile, setIsLoadingFile] = React.useState(false)
  const [isSavingFile, setIsSavingFile] = React.useState(false)
  const [textDraft, setTextDraft] = React.useState("")
  const contentKey = React.useMemo(() => safeStringify(value ?? defaultValue ?? ""), [defaultValue, value])
  const initialContent = React.useMemo(() => normalizeDocumentContent(value ?? defaultValue ?? ""), [contentKey])
  const [docSnapshot, setDocSnapshot] = React.useState(() => summarizeDocument(initialContent))
  const latestContentRef = React.useRef(initialContent)
  const suppressChangeRef = React.useRef(false)
  const fallbackClient = React.useMemo(() => client ?? (null as unknown as LemmaClient), [client])
  const scopedClient = React.useMemo(() => client ? (podId ? client.withPod(podId) : client) : null, [client, podId])

  const editable = intent !== "read"
  const isCreateIntent = intent === "create"
  const resolvedTitle = titleValue ?? uncontrolledTitle
  const resolvedSummary = summaryValue ?? uncontrolledSummary
  const canUseBuiltinInsertPicker = Boolean(client)
  const filePath = fileOptions?.path?.trim() ?? ""
  const fileNamespace = fileOptions?.namespace ?? "POD"
  const fileEnabled = Boolean(fileOptions && (fileOptions.enabled ?? true) && scopedClient && (filePath || isCreateIntent))
  const fileState = useFile({
    client: fallbackClient,
    podId,
    path: filePath,
    namespace: fileNamespace,
    enabled: Boolean(fileEnabled && filePath && !fileOptions?.file && !isCreateIntent),
  })
  const resolvedFile = localFile ?? fileOptions?.file ?? fileState.file
  const fileFormat = inferWorkspaceFileFormat(fileOptions?.format ?? "auto", resolvedFile, filePath)
  const isStructuredFile = Boolean(fileOptions && (isCreateIntent || fileFormat === "structured"))
  const isTextFile = Boolean(fileOptions && !isCreateIntent && (fileFormat === "markdown" || fileFormat === "text"))
  const isInlinePreviewFile = Boolean(fileOptions && !isCreateIntent && !isStructuredFile && !isTextFile)
  const usesStructuredEditor = !fileOptions || isStructuredFile
  const fileDisplayPath = filePath || buildWorkspaceFilePath(fileOptions?.defaultDirectoryPath, fileOptions?.defaultFileName)
  const resolvedPathLabel = pathLabel ?? (fileOptions ? displayWorkspacePath(fileDisplayPath) : undefined)
  const resolvedLastEditedLabel = lastEditedLabel ?? (resolvedFile?.updated_at ? `Updated ${formatWorkspaceDate(resolvedFile.updated_at)}` : undefined)

  const editor = React.useMemo(() => {
    return createEditor({
      extension: defineDocumentExtension(placeholder, editable),
      defaultContent: latestContentRef.current,
    }) as LemmaRichDocumentEditor
  }, [editable, placeholder])

  const setTitle = React.useCallback((nextTitle: string) => {
    if (titleValue == null) setUncontrolledTitle(nextTitle)
    onTitleChange?.(nextTitle)
  }, [onTitleChange, titleValue])

  const setSummary = React.useCallback((nextSummary: string) => {
    if (summaryValue == null) setUncontrolledSummary(nextSummary)
    onSummaryChange?.(nextSummary)
  }, [onSummaryChange, summaryValue])

  const emitChange = React.useCallback(() => {
    const json = editor.getDocJSON()
    const text = documentTextFromContent(json)
    const html = getDocumentHTML(editor)
    latestContentRef.current = json
    setDocSnapshot(documentStats(text))
    onChange?.({ json, html, text })
  }, [editor, onChange])

  const handleDocChange = React.useCallback(() => {
    if (suppressChangeRef.current) return
    emitChange()
  }, [emitChange])

  useDocChange(handleDocChange, { editor })

  React.useEffect(() => {
    const nextContent = normalizeDocumentContent(value ?? defaultValue ?? "")
    if (safeStringify(editor.getDocJSON()) === safeStringify(nextContent)) return
    suppressChangeRef.current = true
    try {
      editor.setContent(nextContent)
      latestContentRef.current = nextContent
      setDocSnapshot(summarizeDocument(nextContent))
    } finally {
      suppressChangeRef.current = false
    }
  }, [contentKey, defaultValue, editor, value])

  const getWorkspaceContext = React.useCallback((): LemmaDocumentWorkspaceContext => {
    const json = editor.getDocJSON()
    return {
      title: resolvedTitle,
      summary: resolvedSummary,
      text: documentTextFromContent(json),
      html: getDocumentHTML(editor),
      json,
      intent,
      pathLabel: resolvedPathLabel,
      file: resolvedFile,
      path: fileDisplayPath,
    }
  }, [editor, fileDisplayPath, intent, resolvedFile, resolvedPathLabel, resolvedSummary, resolvedTitle])

  const loadPodFile = React.useCallback(async (signal?: AbortSignal) => {
    if (!fileEnabled || !scopedClient || !filePath || isCreateIntent) return

    setIsLoadingFile(true)
    setFileError(null)
    setFileContent(null)
    setObjectUrl((previous) => {
      if (previous) URL.revokeObjectURL(previous)
      return null
    })

    try {
      const nextFile = fileOptions?.file ?? await scopedClient.files.get(filePath, { namespace: fileNamespace })
      if (signal?.aborted) return
      setLocalFile(nextFile)

      if (fileFormat === "download") return

      if (fileFormat === "html") {
        const html = await scopedClient.files.converted.render(filePath, { namespace: fileNamespace })
        if (signal?.aborted) return
        setFileContent(typeof html === "string" ? html : String(html ?? ""))
        return
      }

      const blob = await scopedClient.files.download(filePath, { namespace: fileNamespace })
      if (signal?.aborted) return

      if (fileFormat === "image" || fileFormat === "pdf") {
        setObjectUrl(URL.createObjectURL(blob))
        return
      }

      const text = await blob.text()
      if (signal?.aborted) return
      setFileContent(text)

      if (fileFormat === "structured") {
        const parsed = parsePersistedWorkspaceDocumentBody(text)
        const nextContent = parsed?.content ?? normalizeDocumentContent(text)
        const nextTitle = parsed?.title ?? humanizeWorkspaceFileName(nextFile.name || nextFile.path)
        const nextSummary = parsed?.summary ?? nextFile.description?.trim() ?? ""
        suppressChangeRef.current = true
        try {
          editor.setContent(nextContent)
          latestContentRef.current = nextContent
          setDocSnapshot(summarizeDocument(nextContent))
          if (titleValue == null) setUncontrolledTitle(nextTitle)
          if (summaryValue == null) setUncontrolledSummary(nextSummary)
          setLoadedFileSnapshot({
            title: nextTitle,
            summary: nextSummary,
            contentKey: safeStringify(nextContent),
            text: documentTextFromContent(nextContent),
            path: nextFile.path,
          })
        } finally {
          suppressChangeRef.current = false
        }
      } else if (fileFormat === "markdown" || fileFormat === "text") {
        setTextDraft(text)
        setLoadedFileSnapshot({
          title: humanizeWorkspaceFileName(nextFile.name || nextFile.path),
          summary: nextFile.description?.trim() ?? "",
          contentKey: "",
          text,
          path: nextFile.path,
        })
      }
    } catch (error) {
      if (signal?.aborted) return
      setFileError(error instanceof Error ? error : new Error("Failed to load pod file."))
    } finally {
      if (!signal?.aborted) setIsLoadingFile(false)
    }
  }, [editor, fileEnabled, fileFormat, fileNamespace, fileOptions?.file, filePath, isCreateIntent, scopedClient, summaryValue, titleValue])

  React.useEffect(() => {
    setLocalFile(null)
    setLoadedFileSnapshot(null)
  }, [filePath])

  React.useEffect(() => {
    if (!fileOptions) return
    if (isCreateIntent) {
      const nextTitle = titleValue ?? defaultTitle
      const nextSummary = summaryValue ?? defaultSummary
      const nextContent = normalizeDocumentContent(value ?? defaultValue ?? "")
      suppressChangeRef.current = true
      try {
        editor.setContent(nextContent)
        latestContentRef.current = nextContent
        setDocSnapshot(summarizeDocument(nextContent))
      } finally {
        suppressChangeRef.current = false
      }
      setLoadedFileSnapshot({
        title: nextTitle,
        summary: nextSummary,
        contentKey: safeStringify(nextContent),
        text: documentTextFromContent(nextContent),
        path: buildWorkspaceFilePath(fileOptions.defaultDirectoryPath, fileOptions.defaultFileName),
      })
      return
    }
    const controller = new AbortController()
    void loadPodFile(controller.signal)
    return () => {
      controller.abort()
      setObjectUrl((previous) => {
        if (previous) URL.revokeObjectURL(previous)
        return null
      })
    }
  }, [
    defaultSummary,
    defaultTitle,
    defaultValue,
    editor,
    fileOptions?.defaultDirectoryPath,
    fileOptions?.defaultFileName,
    isCreateIntent,
    loadPodFile,
    summaryValue,
    titleValue,
    value,
  ])

  const fileMetadata = React.useMemo(() => {
    if (!resolvedFile) return metadata
    return [
      ...metadata,
      { label: "Path", value: displayWorkspacePath(resolvedFile.path) },
      { label: "Type", value: resolvedFile.mime_type ?? fileFormat },
      { label: "Size", value: typeof resolvedFile.size_bytes === "number" ? formatBytes(resolvedFile.size_bytes) : "Unknown" },
      { label: "Search", value: resolvedFile.search_enabled === false ? "Disabled" : resolvedFile.indexed_at ? `Indexed ${formatWorkspaceDate(resolvedFile.indexed_at)}` : "Index pending" },
    ]
  }, [fileFormat, metadata, resolvedFile])

  const structuredFileDirty = Boolean(
    fileOptions &&
    isStructuredFile &&
    loadedFileSnapshot &&
    (resolvedTitle !== loadedFileSnapshot.title ||
      resolvedSummary !== loadedFileSnapshot.summary ||
      safeStringify(latestContentRef.current) !== loadedFileSnapshot.contentKey),
  )
  const textFileDirty = Boolean(fileOptions && isTextFile && loadedFileSnapshot && textDraft !== loadedFileSnapshot.text)
  const effectiveSaveState: LemmaDocumentWorkspaceSaveState = fileOptions
    ? fileError
      ? "error"
      : isSavingFile
        ? "saving"
        : structuredFileDirty || textFileDirty
          ? "dirty"
          : "saved"
    : controlledSaveState

  const savePodFile = React.useCallback(async () => {
    if (!fileOptions || !scopedClient || isSavingFile) return
    setIsSavingFile(true)
    setFileError(null)
    try {
      const context = getWorkspaceContext()
      const targetPath = filePath || buildWorkspaceFilePath(fileOptions.defaultDirectoryPath, fileOptions.defaultFileName || documentFileNameFromTitle(context.title))
      let updated: FileResponse

      if (isTextFile) {
        updated = await scopedClient.files.update(targetPath, {
          file: new Blob([textDraft], { type: inferWorkspaceTextMimeType(resolvedFile?.mime_type, targetPath, fileFormat) }),
          namespace: fileNamespace,
        })
        setTextDraft(textDraft)
        setLoadedFileSnapshot({
          title: humanizeWorkspaceFileName(updated.name || updated.path),
          summary: updated.description?.trim() ?? "",
          contentKey: "",
          text: textDraft,
          path: updated.path,
        })
      } else {
        const nextTitle = context.title.trim() || humanizeWorkspaceFileName(targetPath)
        const nextSummary = context.summary.trim()
        const body = buildWorkspaceDocumentBody({
          title: nextTitle,
          summary: nextSummary,
          content: context.json,
        })
        const blob = new Blob([body], { type: "application/json;charset=utf-8" })
        if (isCreateIntent || !filePath) {
          updated = await scopedClient.files.upload(blob, {
            name: fileOptions.defaultFileName || documentFileNameFromTitle(nextTitle),
            directoryPath: normalizePath(fileOptions.defaultDirectoryPath),
            description: nextSummary || undefined,
            searchEnabled: fileOptions.searchEnabled ?? true,
            namespace: fileNamespace,
          })
          onCreateSuccess?.(updated, { ...context, file: updated, path: updated.path })
        } else {
          updated = await scopedClient.files.update(filePath, {
            file: blob,
            description: nextSummary || undefined,
            name: filePath.toLowerCase().endsWith(".lemma-doc.json") ? undefined : documentFileNameFromTitle(nextTitle),
            namespace: fileNamespace,
          })
        }
        setLoadedFileSnapshot({
          title: nextTitle,
          summary: nextSummary,
          contentKey: safeStringify(context.json),
          text: context.text,
          path: updated.path,
        })
      }

      setLocalFile(updated)
      onSaveSuccess?.(updated, { ...getWorkspaceContext(), file: updated, path: updated.path })
      await fileState.refresh?.({ namespace: fileNamespace })
    } catch (error) {
      setFileError(error instanceof Error ? error : new Error("Failed to save pod file."))
    } finally {
      setIsSavingFile(false)
    }
  }, [
    fileFormat,
    fileOptions,
    filePath,
    fileNamespace,
    fileState,
    getWorkspaceContext,
    isCreateIntent,
    isSavingFile,
    isTextFile,
    onCreateSuccess,
    onSaveSuccess,
    resolvedFile?.mime_type,
    scopedClient,
    textDraft,
  ])

  const handleDownload = React.useCallback(async () => {
    if (!scopedClient || !filePath) return
    const blob = await scopedClient.files.download(filePath, { namespace: fileNamespace })
    const url = URL.createObjectURL(blob)
    const anchor = document.createElement("a")
    anchor.href = url
    anchor.download = downloadFileNameFromPath(filePath)
    anchor.click()
    URL.revokeObjectURL(url)
  }, [fileNamespace, filePath, scopedClient])

  const effectiveOnSave = fileOptions ? savePodFile : onSave
  const effectiveSaveDisabled = fileOptions
    ? saveDisabled || isLoadingFile || isSavingFile || (!structuredFileDirty && !textFileDirty && !isCreateIntent)
    : saveDisabled

  const fileTreeState = useFileTree({
    client: fallbackClient,
    podId,
    enabled: canUseBuiltinInsertPicker && filePickerOpen,
    rootPath: normalizePath(filePickerRootPath),
    namespace: fileNamespace,
  })

  const pickerFilesState = useFiles({
    client: fallbackClient,
    podId,
    enabled: canUseBuiltinInsertPicker && filePickerOpen,
    directoryPath: filePickerDirectory,
    namespace: fileNamespace,
  })

  const tablesState = useTables({
    client: fallbackClient,
    podId,
    enabled: canUseBuiltinInsertPicker && recordsPickerOpen,
  })

  const folderEntries = React.useMemo(
    () => flattenDirectoryTree(fileTreeState.tree),
    [fileTreeState.tree],
  )

  const selectedFile = React.useMemo(
    () => pickerFilesState.files.find((file) => file.path === selectedFilePath) ?? null,
    [pickerFilesState.files, selectedFilePath],
  )

  const selectedTable = React.useMemo(
    () => tablesState.tables.find((table) => table.name === selectedTableName) ?? null,
    [selectedTableName, tablesState.tables],
  )

  const openBuiltinFilePicker = React.useCallback(() => {
    setFilePickerDirectory(normalizePath(filePickerRootPath))
    setSelectedFilePath(null)
    setFileReferenceTitle("")
    setFileReferenceDescription("")
    setFileReferenceMeta("")
    setFilePickerOpen(true)
  }, [filePickerRootPath])

  const openBuiltinRecordsPicker = React.useCallback(() => {
    setRecordsReferenceTitle("")
    setRecordsReferenceDescription("")
    setRecordsFiltersText("")
    setRecordsSortsText("")
    setSelectedRecordColumns([])
    setSelectedTableName("")
    setRecordsPickerOpen(true)
  }, [])

  React.useEffect(() => {
    setFilePickerDirectory(normalizePath(filePickerRootPath))
  }, [filePickerRootPath])

  React.useEffect(() => {
    setSelectedFilePath(null)
  }, [filePickerDirectory])

  React.useEffect(() => {
    if (!selectedFile) return
    setFileReferenceTitle((current) => current.trim() ? current : displayFileLabel(selectedFile))
  }, [selectedFile])

  React.useEffect(() => {
    if (!recordsPickerOpen || selectedTableName || tablesState.tables.length === 0) return
    setSelectedTableName(tablesState.tables[0]?.name ?? "")
  }, [recordsPickerOpen, selectedTableName, tablesState.tables])

  React.useEffect(() => {
    if (!selectedTable) return
    setRecordsReferenceTitle((current) => current.trim() ? current : `Filtered ${selectedTable.name}`)
    setSelectedRecordColumns(selectedTable.columns.slice(0, 4).map((column) => column.name))
  }, [selectedTable])

  const confirmFileReference = React.useCallback(() => {
    if (!selectedFile) return
    insertFileBlock(editor, {
      title: fileReferenceTitle.trim() || displayFileLabel(selectedFile),
      path: selectedFile.path,
      description: fileReferenceDescription.trim() || undefined,
      meta: fileReferenceMeta.trim() || formatFileMeta(selectedFile),
    })
    setFilePickerOpen(false)
    window.setTimeout(emitChange, 0)
  }, [editor, emitChange, fileReferenceDescription, fileReferenceMeta, fileReferenceTitle, selectedFile])

  const confirmRecordsReference = React.useCallback(() => {
    if (!selectedTable) return
    insertRecordsBlock(editor, {
      table: selectedTable.name,
      title: recordsReferenceTitle.trim() || `Filtered ${selectedTable.name}`,
      description: recordsReferenceDescription.trim() || undefined,
      filters: parseLineList(recordsFiltersText),
      columns: selectedRecordColumns,
      sorts: parseLineList(recordsSortsText),
    })
    setRecordsPickerOpen(false)
    window.setTimeout(emitChange, 0)
  }, [
    editor,
    emitChange,
    recordsFiltersText,
    recordsReferenceDescription,
    recordsReferenceTitle,
    recordsSortsText,
    selectedRecordColumns,
    selectedTable,
  ])

  const defaultCommands = React.useMemo<LemmaDocumentWorkspaceCommand[]>(() => [
    {
      id: "paragraph",
      label: "Text",
      description: "Plain writing block",
      icon: <Pilcrow className="size-4" />,
      keywords: ["paragraph", "body"],
      run: (nextEditor) => nextEditor.commands.setParagraph(),
    },
    {
      id: "heading-1",
      label: "Heading 1",
      description: "Large section title",
      icon: <Heading1 className="size-4" />,
      keywords: ["title", "h1"],
      run: (nextEditor) => nextEditor.commands.toggleHeading({ level: 1 }),
    },
    {
      id: "heading-2",
      label: "Heading 2",
      description: "Section heading",
      icon: <Heading2 className="size-4" />,
      keywords: ["subtitle", "h2"],
      run: (nextEditor) => nextEditor.commands.toggleHeading({ level: 2 }),
    },
    {
      id: "bullet-list",
      label: "Bullet list",
      description: "Simple unordered list",
      icon: <List className="size-4" />,
      keywords: ["bullets"],
      run: (nextEditor) => nextEditor.commands.toggleList({ kind: "bullet" }),
    },
    {
      id: "ordered-list",
      label: "Numbered list",
      description: "Steps or ranked items",
      icon: <ListOrdered className="size-4" />,
      keywords: ["ordered", "numbered"],
      run: (nextEditor) => nextEditor.commands.toggleList({ kind: "ordered" }),
    },
    {
      id: "task-list",
      label: "Task list",
      description: "Checklist for follow-up",
      icon: <CheckSquare className="size-4" />,
      keywords: ["todo", "checklist"],
      run: (nextEditor) => nextEditor.commands.toggleList({ kind: "task" }),
    },
    {
      id: "callout",
      label: "Callout",
      description: "Decision, risk, or note",
      icon: <Quote className="size-4" />,
      keywords: ["quote", "note"],
      run: (nextEditor) => insertBlockText(nextEditor, "Add an important note, decision, or caveat.", "blockquote"),
    },
    {
      id: "table",
      label: "Table",
      description: "Structured rows and columns",
      icon: <Table2 className="size-4" />,
      keywords: ["grid"],
      run: (nextEditor) => insertSeededTable(nextEditor),
    },
    // Temporarily disabled until the file/records embed flow is rebuilt.
    // {
    //   id: "file-reference",
    //   label: "File",
    //   description: "Link a pod file",
    //   icon: <Link2 className="size-4" />,
    //   keywords: ["attachment", "docstore", "pdf", "image"],
    //   run: async (nextEditor, context) => {
    //     if (canUseBuiltinInsertPicker) {
    //       openBuiltinFilePicker()
    //       return
    //     }
    //     if (onInsertFile) {
    //       const fileBlock = await onInsertFile(context)
    //       if (fileBlock) {
    //         insertFileBlock(nextEditor, fileBlock)
    //       }
    //       return
    //     }
    //     insertFileBlock(nextEditor, {
    //       title: "Pod file",
    //       path: "/documents/example.md",
    //       description: "Linked from the document workspace.",
    //       meta: "Connect a file picker to insert a real pod file here.",
    //     })
    //   },
    // },
    // {
    //   id: "record-view",
    //   label: "Records",
    //   description: "Embed a filtered pod data view",
    //   icon: <Database className="size-4" />,
    //   keywords: ["table", "data", "filtered", "view"],
    //   run: async (nextEditor, context) => {
    //     if (canUseBuiltinInsertPicker) {
    //       openBuiltinRecordsPicker()
    //       return
    //     }
    //     if (onInsertRecords) {
    //       const recordsBlock = await onInsertRecords(context)
    //       if (recordsBlock) {
    //         insertRecordsBlock(nextEditor, recordsBlock)
    //       }
    //       return
    //     }
    //     insertRecordsBlock(nextEditor, {
    //       title: "Open issues",
    //       table: "issues",
    //       description: "Embed a filtered records view from the host desk.",
    //       filters: ["status = open", "priority in (high, urgent)"],
    //       columns: ["identifier", "title", "status", "assignee"],
    //       sorts: ["updated_at desc"],
    //     })
    //   },
    // },
  ], [canUseBuiltinInsertPicker, onInsertFile, onInsertRecords, openBuiltinFilePicker, openBuiltinRecordsPicker])

  const commandItems = React.useMemo(() => [...defaultCommands, ...commands], [commands, defaultCommands])
  const filteredCommands = React.useMemo(() => {
    const query = commandQuery.trim().toLowerCase()
    if (!query) return commandItems
    return commandItems.filter((command) => {
      const haystack = [
        command.id,
        plainNodeText(command.label),
        plainNodeText(command.description),
        ...(command.keywords ?? []),
      ].join(" ").toLowerCase()
      return haystack.includes(query)
    })
  }, [commandItems, commandQuery])

  const executeCommand = React.useCallback((command: LemmaDocumentWorkspaceCommand) => {
    if (command.disabled || !editable) return
    setCommandMenuOpen(false)
    setCommandQuery("")
    void Promise.resolve(command.run(editor, getWorkspaceContext())).finally(() => {
      window.setTimeout(emitChange, 0)
    })
  }, [editable, editor, emitChange, getWorkspaceContext])

  const askAssistant = React.useCallback(() => {
    if (!onAskAssistant) return
    onAskAssistant(getWorkspaceContext())
  }, [getWorkspaceContext, onAskAssistant])

  const openComments = React.useCallback(() => {
    if (!onOpenComments) return
    onOpenComments(getWorkspaceContext())
  }, [getWorkspaceContext, onOpenComments])

  const openActivity = React.useCallback(() => {
    if (!onOpenActivity) return
    onOpenActivity(getWorkspaceContext())
  }, [getWorkspaceContext, onOpenActivity])

  const content = (
    <div
      data-appearance={appearance}
      data-density={density}
      data-intent={intent}
      data-mode={mode}
      data-radius={radius}
      className={cn(
        "lemma-document-workspace relative flex min-h-0 flex-col overflow-hidden",
        surfaceClassName(appearance, radius, mode),
        mode === "page" ? "min-h-[calc(100vh-9rem)]" : "h-full",
        className,
      )}
    >
      <ScopedDocumentStyles />
      <div className="flex shrink-0 items-center justify-between gap-3 border-b border-border/40 bg-background/80 px-4 py-1.5 backdrop-blur-sm">
        <div className="flex min-w-0 items-center gap-2 text-xs text-muted-foreground">
          {resolvedPathLabel ? <span className="truncate">{resolvedPathLabel}</span> : null}
          {resolvedPathLabel && resolvedLastEditedLabel ? <span>·</span> : null}
          {resolvedLastEditedLabel ? <span>{resolvedLastEditedLabel}</span> : null}
          <span className={cn(effectiveSaveState === "dirty" ? "font-medium text-foreground" : effectiveSaveState === "error" ? "text-destructive" : null)}>{saveStateLabel(effectiveSaveState, intent)}</span>
          <span>·</span>
          <span>{isTextFile ? `${textDraft.length} characters` : `${docSnapshot.words} words`}</span>
        </div>
        <div className="flex shrink-0 items-center gap-1.5">
          {headerActions}
          {fileOptions && filePath ? (
            <>
              <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={() => void loadPodFile()} disabled={isLoadingFile}>
                <RefreshCw className={cn("size-3.5", isLoadingFile ? "animate-spin" : null)} />
                Refresh
              </Button>
              <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={() => void handleDownload()}>
                <Download className="size-3.5" />
                Download
              </Button>
              {onOpenExternal ? (
                <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={() => onOpenExternal(resolvedFile, filePath)}>
                  <ExternalLink className="size-3.5" />
                  Open
                </Button>
              ) : null}
            </>
          ) : null}
          {onAskAssistant ? (
            <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={askAssistant}>
              <WandSparkles className="size-3.5" />
              Ask
            </Button>
          ) : null}
          {editable && usesStructuredEditor ? (
            <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={() => setToolbarVisible((v) => !v)}>
              <Pilcrow className="size-3.5" />
              Format
            </Button>
          ) : null}
          <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={() => setInspectorOpen((current) => !current)}>
            <PanelRightOpen className="size-3.5" />
            Details
          </Button>
          {effectiveOnSave ? (
            <Button size="sm" className="h-7 gap-1.5 px-3 text-xs" onClick={() => void effectiveOnSave()} disabled={effectiveSaveDisabled || effectiveSaveState === "saving"}>
              {effectiveSaveState === "saving" ? <RefreshCw className="size-3.5 animate-spin" /> : <Save className="size-3.5" />}
              {isCreateIntent ? "Create" : "Save"}
            </Button>
          ) : null}
        </div>
      </div>

      <div className="min-h-0 flex-1 overflow-auto">
        <div className={cn("mx-auto w-full max-w-[860px]", contentPaddingClassName(density))}>
          {fileError ? (
            <WorkspaceFileError error={fileError} radius={radius} onDownload={filePath ? handleDownload : undefined} />
          ) : isLoadingFile ? (
            <WorkspaceFileLoading />
          ) : isInlinePreviewFile ? (
            <WorkspaceFilePreview
              kind={fileFormat}
              title={resolvedFile?.name ?? fileNameFromPath(filePath)}
              content={fileContent}
              objectUrl={objectUrl}
              radius={radius}
              onDownload={handleDownload}
            />
          ) : isTextFile ? (
            <WorkspaceTextFileEditor
              value={textDraft}
              onChange={setTextDraft}
              editable={editable && (fileOptions?.textEditable ?? true)}
              density={density}
              radius={radius}
            />
          ) : editable ? (
            <input
              value={resolvedTitle}
              onChange={(event) => setTitle(event.target.value)}
              placeholder="Untitled"
              className={cn("w-full border-0 bg-transparent font-bold tracking-tight text-foreground outline-none placeholder:text-muted-foreground/50", inlineTitleClassName(density))}
            />
          ) : (
            <h1 className={cn("font-bold tracking-tight text-foreground", inlineTitleClassName(density))}>{resolvedTitle || "Untitled"}</h1>
          )}

          {editable ? (
            <input
              value={resolvedSummary}
              onChange={(event) => setSummary(event.target.value)}
              placeholder="Add a summary..."
              className={cn("mt-1 w-full border-0 bg-transparent text-muted-foreground outline-none placeholder:text-muted-foreground/40", inlineSummaryClassName(density))}
            />
          ) : resolvedSummary ? (
            <p className={cn("mt-1 text-muted-foreground", inlineSummaryClassName(density))}>{resolvedSummary}</p>
          ) : null}

          {editable && toolbarVisible && usesStructuredEditor ? (
            <div className={cn("sticky top-0 z-20 mt-4 -mx-1 border border-border/50 bg-background/95 backdrop-blur-sm", radiusClassName(radius, "control"))}>
              <ProseKitToolbar editor={editor} onOpenInsert={() => setCommandMenuOpen((current) => !current)} />
              {commandMenuOpen ? (
                <CommandMenu
                  query={commandQuery}
                  onQueryChange={setCommandQuery}
                  commands={filteredCommands}
                  radius={radius}
                  density={density}
                  onClose={() => setCommandMenuOpen(false)}
                  onSelect={executeCommand}
                />
              ) : null}
            </div>
          ) : null}

          {usesStructuredEditor ? (
            <ProseKit editor={editor}>
              <div className="lemma-prosekit-editor relative">
                <div
                  ref={editor.mount}
                  data-lemma-editor=""
                  aria-label="Document body"
                  className={cn(
                    "ProseMirror lemma-prosekit-mount min-h-[70vh] outline-none",
                    editable ? "cursor-text" : "cursor-default",
                  )}
                  onFocus={() => setEditorFocused(true)}
                  onBlur={() => setEditorFocused(false)}
                />
                {editable ? (
                  <>
                    <SlashMenu commands={commandItems} onSelect={executeCommand} />
                    <DocumentTableHandle />
                  </>
                ) : null}
              </div>
            </ProseKit>
          ) : null}
        </div>
      </div>

      {inspectorOpen ? (
        <aside className={cn("absolute bottom-0 right-0 top-0 z-30 w-[320px] overflow-auto border-l border-border/60 bg-background shadow-lg", inspectorSlideClassName(editorFocused))}>
          <div className={cn("space-y-4", inspectorPaddingClassName(density))}>
            <div className="flex items-center justify-between">
              <span className="text-sm font-medium text-foreground">Details</span>
              <Button variant="ghost" size="sm" className="h-6 w-6 p-0" onClick={() => setInspectorOpen(false)}>
                <X className="size-3.5" />
              </Button>
            </div>
            <InspectorCard title="Properties" icon={<Columns3 className="size-4" />} radius={radius}>
              <InspectorRow label="Mode" value={intentLabel(intent)} />
              <InspectorRow label="Words" value={String(docSnapshot.words)} />
              <InspectorRow label="Characters" value={String(docSnapshot.characters)} />
              {status ? <InspectorRow label="Status" value={status} /> : null}
              {fileMetadata.map((item, index) => (
                <InspectorRow key={index} label={item.label} value={item.value} />
              ))}
            </InspectorCard>

            {references.length > 0 ? (
              <InspectorCard title="References" icon={<FileText className="size-4" />} radius={radius}>
                <ReferenceList items={references} radius={radius} />
              </InspectorCard>
            ) : null}

            {backlinks.length > 0 ? (
              <InspectorCard title="Backlinks" icon={<Hash className="size-4" />} radius={radius}>
                <ReferenceList items={backlinks} radius={radius} />
              </InspectorCard>
            ) : null}

            {(onOpenComments || onOpenActivity) ? (
              <InspectorCard title="Collaboration" icon={<MessageSquare className="size-4" />} radius={radius}>
                <div className="grid gap-2">
                  {onOpenComments ? (
                    <Button type="button" variant="outline" size="sm" onClick={openComments}>
                      Comments
                    </Button>
                  ) : null}
                  {onOpenActivity ? (
                    <Button type="button" variant="outline" size="sm" onClick={openActivity}>
                      Activity
                    </Button>
                  ) : null}
                </div>
              </InspectorCard>
            ) : null}

            <InspectorCard title="Agent" icon={<WandSparkles className="size-4" />} radius={radius}>
              {assistantContext.length > 0 ? (
                <div className="space-y-2">
                  {assistantContext.map((item, index) => (
                    <div key={index} className={cn("border border-border bg-muted/20 p-3", radiusClassName(radius, "surface"))}>
                      <p className="text-xs font-medium uppercase text-muted-foreground">{item.label}</p>
                      {item.value ? <p className="mt-1 text-sm text-foreground/85">{item.value}</p> : null}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-sm text-muted-foreground">Use the active document, selected blocks, linked files, and records as assistant context.</p>
              )}

              {(onAskAssistant || assistantActions.length > 0) ? (
                <div className="mt-3 grid gap-2">
                  {onAskAssistant ? (
                    <Button type="button" size="sm" onClick={askAssistant}>
                      Ask about this
                    </Button>
                  ) : null}
                  {assistantActions.map((action) => (
                    <button
                      key={action.id}
                      type="button"
                      disabled={action.disabled}
                      onClick={() => action.onSelect(getWorkspaceContext())}
                      className={cn("border border-border bg-background p-3 text-left text-sm transition-colors hover:bg-muted/45 disabled:pointer-events-none disabled:opacity-50", radiusClassName(radius, "surface"))}
                    >
                      <span className="font-medium text-foreground">{action.label}</span>
                      {action.description ? <span className="mt-1 block text-xs text-muted-foreground">{action.description}</span> : null}
                    </button>
                  ))}
                </div>
              ) : null}
            </InspectorCard>
          </div>
        </aside>
      ) : null}

      <Dialog open={filePickerOpen} onOpenChange={setFilePickerOpen}>
        <DialogContent className="max-h-[90vh] overflow-hidden sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>Insert file reference</DialogTitle>
            <DialogDescription>Pick a folder, choose a file, then save the reference into this document.</DialogDescription>
          </DialogHeader>
          <div className="grid min-h-0 gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
            <div className="min-h-0 overflow-auto rounded-lg border border-border/60 bg-muted/15 p-2">
              <p className="px-2 py-1 text-xs font-medium uppercase tracking-widest text-muted-foreground">Folders</p>
              <div className="mt-1 space-y-1">
                {folderEntries.length > 0 ? folderEntries.map((entry) => (
                  <button
                    key={entry.path}
                    type="button"
                    onClick={() => setFilePickerDirectory(entry.path)}
                    className={cn(
                      "flex w-full items-center gap-2 px-2 py-2 text-left text-sm transition-colors hover:bg-muted/55",
                      radiusClassName(radius, "control"),
                      filePickerDirectory === entry.path ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
                    )}
                    style={{ paddingLeft: `${entry.depth * 14 + 8}px` }}
                  >
                    <FileText className="size-4 shrink-0" />
                    <span className="truncate">{entry.path === "/" ? "Root" : entry.name}</span>
                  </button>
                )) : (
                  <p className="px-2 py-3 text-sm text-muted-foreground">No folders found.</p>
                )}
              </div>
            </div>

            <div className="min-h-0 space-y-4">
              <div className="rounded-lg border border-border/60 bg-muted/10 p-3">
                <p className="text-xs font-medium uppercase tracking-widest text-muted-foreground">Current folder</p>
                <p className="mt-1 truncate text-sm text-foreground">{filePickerDirectory}</p>
              </div>

              <div className="min-h-0 overflow-auto rounded-lg border border-border/60">
                <div className="border-b border-border/60 px-3 py-2 text-sm font-medium text-foreground">Files</div>
                <div className="max-h-64 overflow-auto p-2">
                  {fileTreeState.error ? (
                    <p className="text-sm text-destructive">{fileTreeState.error.message}</p>
                  ) : pickerFilesState.isLoading ? (
                    <p className="text-sm text-muted-foreground">Loading files...</p>
                  ) : pickerFilesState.error ? (
                    <p className="text-sm text-destructive">{pickerFilesState.error.message}</p>
                  ) : pickerFilesState.files.filter((file) => !isDirectoryLike(file)).length === 0 ? (
                    <p className="text-sm text-muted-foreground">No files in this folder.</p>
                  ) : (
                    pickerFilesState.files.filter((file) => !isDirectoryLike(file)).map((file) => (
                      <button
                        key={file.id ?? file.path}
                        type="button"
                        onClick={() => setSelectedFilePath(file.path)}
                        className={cn(
                          "mb-1 flex w-full items-start justify-between gap-3 border px-3 py-2 text-left transition-colors hover:bg-muted/45",
                          radiusClassName(radius, "control"),
                          selectedFilePath === file.path ? "border-border bg-muted/45" : "border-transparent",
                        )}
                      >
                        <span className="min-w-0">
                          <span className="block truncate text-sm font-medium text-foreground">{displayFileLabel(file)}</span>
                          <span className="block truncate text-xs text-muted-foreground">{file.path}</span>
                        </span>
                        <span className="shrink-0 text-xs text-muted-foreground">{formatBytes(file.size_bytes)}</span>
                      </button>
                    ))
                  )}
                </div>
              </div>

              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-2">
                  <p className="text-sm font-medium text-foreground">Reference title</p>
                  <Input value={fileReferenceTitle} onChange={(event) => setFileReferenceTitle(event.target.value)} placeholder="Quarterly plan.pdf" />
                </div>
                <div className="space-y-2">
                  <p className="text-sm font-medium text-foreground">Meta</p>
                  <Input value={fileReferenceMeta} onChange={(event) => setFileReferenceMeta(event.target.value)} placeholder="PDF | Updated Apr 20" />
                </div>
              </div>
              <div className="space-y-2">
                <p className="text-sm font-medium text-foreground">Description</p>
                <Textarea value={fileReferenceDescription} onChange={(event) => setFileReferenceDescription(event.target.value)} placeholder="Why this file matters in the document context." rows={3} />
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setFilePickerOpen(false)}>Cancel</Button>
            <Button type="button" onClick={confirmFileReference} disabled={!selectedFile}>Add file reference</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>

      <Dialog open={recordsPickerOpen} onOpenChange={setRecordsPickerOpen}>
        <DialogContent className="max-h-[90vh] overflow-hidden sm:max-w-4xl">
          <DialogHeader>
            <DialogTitle>Insert records reference</DialogTitle>
            <DialogDescription>Pick a table, define filters, and save a filtered records block into this document.</DialogDescription>
          </DialogHeader>
          <div className="grid min-h-0 gap-4 lg:grid-cols-[16rem_minmax(0,1fr)]">
            <div className="min-h-0 overflow-auto rounded-lg border border-border/60 bg-muted/15 p-2">
              <p className="px-2 py-1 text-xs font-medium uppercase tracking-widest text-muted-foreground">Tables</p>
              <div className="mt-1 space-y-1">
                {tablesState.isLoading ? (
                  <p className="px-2 py-3 text-sm text-muted-foreground">Loading tables...</p>
                ) : tablesState.error ? (
                  <p className="px-2 py-3 text-sm text-destructive">{tablesState.error.message}</p>
                ) : tablesState.tables.length === 0 ? (
                  <p className="px-2 py-3 text-sm text-muted-foreground">No tables found.</p>
                ) : (
                  tablesState.tables.map((table) => (
                    <button
                      key={table.id}
                      type="button"
                      onClick={() => setSelectedTableName(table.name)}
                      className={cn(
                        "flex w-full items-center justify-between gap-2 px-3 py-2 text-left text-sm transition-colors hover:bg-muted/55",
                        radiusClassName(radius, "control"),
                        selectedTableName === table.name ? "bg-background text-foreground shadow-sm" : "text-muted-foreground",
                      )}
                    >
                      <span className="truncate">{table.name}</span>
                      <span className="shrink-0 text-[10px] uppercase tracking-widest">{table.columns.length} cols</span>
                    </button>
                  ))
                )}
              </div>
            </div>

            <div className="min-h-0 space-y-4">
              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-2">
                  <p className="text-sm font-medium text-foreground">Reference title</p>
                  <Input value={recordsReferenceTitle} onChange={(event) => setRecordsReferenceTitle(event.target.value)} placeholder="Open issues" />
                </div>
                <div className="space-y-2">
                  <p className="text-sm font-medium text-foreground">Table</p>
                  <Input value={selectedTable?.name ?? ""} readOnly placeholder="Choose a table" />
                </div>
              </div>

              <div className="space-y-2">
                <p className="text-sm font-medium text-foreground">Description</p>
                <Textarea value={recordsReferenceDescription} onChange={(event) => setRecordsReferenceDescription(event.target.value)} placeholder="What this filtered records view represents." rows={2} />
              </div>

              <div className="grid gap-3 md:grid-cols-2">
                <div className="space-y-2">
                  <p className="text-sm font-medium text-foreground">Filters</p>
                  <Textarea value={recordsFiltersText} onChange={(event) => setRecordsFiltersText(event.target.value)} placeholder={"status = open\npriority in (high, urgent)"} rows={5} />
                </div>
                <div className="space-y-2">
                  <p className="text-sm font-medium text-foreground">Sort</p>
                  <Textarea value={recordsSortsText} onChange={(event) => setRecordsSortsText(event.target.value)} placeholder={"updated_at desc\npriority asc"} rows={5} />
                </div>
              </div>

              <div className="space-y-2">
                <p className="text-sm font-medium text-foreground">Columns</p>
                <div className="max-h-44 overflow-auto rounded-lg border border-border/60 p-2">
                  {selectedTable ? (
                    <div className="grid gap-2 md:grid-cols-2">
                      {selectedTable.columns.map((column) => {
                        const checked = selectedRecordColumns.includes(column.name)
                        return (
                          <label key={column.name} className={cn("flex items-center gap-2 border border-transparent px-2 py-1.5 text-sm hover:bg-muted/45", radiusClassName(radius, "control"))}>
                            <input
                              type="checkbox"
                              checked={checked}
                              onChange={(event) => {
                                setSelectedRecordColumns((current) => event.target.checked
                                  ? Array.from(new Set([...current, column.name]))
                                  : current.filter((name) => name !== column.name))
                              }}
                            />
                            <span className="truncate">{column.name}</span>
                          </label>
                        )
                      })}
                    </div>
                  ) : (
                    <p className="text-sm text-muted-foreground">Choose a table to pick columns.</p>
                  )}
                </div>
              </div>
            </div>
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => setRecordsPickerOpen(false)}>Cancel</Button>
            <Button type="button" onClick={confirmRecordsReference} disabled={!selectedTableName}>Add records reference</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )

  if (mode === "modal") {
    return (
      <Dialog open={open ?? true} onOpenChange={onOpenChange}>
        <DialogContent
          className={cn(
            "!h-[96vh] !max-h-[96vh] !w-[calc(100vw-1.5rem)] !max-w-[96vw] gap-0 overflow-hidden p-0",
            overlayClassName(appearance, radius),
          )}
        >
          {content}
        </DialogContent>
      </Dialog>
    )
  }

  return <div className="mx-auto flex h-full w-full max-w-[1500px] flex-col">{content}</div>
}

function WorkspaceFileLoading() {
  return (
    <div className="grid gap-3 py-8">
      <div className="h-8 w-1/3 animate-pulse rounded-md bg-muted" />
      <div className="h-24 w-full animate-pulse rounded-md bg-muted" />
      <div className="h-[28rem] w-full animate-pulse rounded-md bg-muted" />
    </div>
  )
}

function WorkspaceFileError({
  error,
  radius,
  onDownload,
}: {
  error: Error
  radius: LemmaDocumentWorkspaceRadius
  onDownload?: () => void | Promise<void>
}) {
  return (
    <div className="flex min-h-[32rem] flex-col items-center justify-center gap-3 text-center">
      <span className={cn("flex size-11 items-center justify-center border border-destructive/30 bg-destructive/10 text-destructive", radiusClassName(radius, "control"))}>
        <AlertCircle className="size-5" />
      </span>
      <div>
        <p className="font-medium text-foreground">Preview unavailable</p>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{error.message}</p>
      </div>
      {onDownload ? (
        <Button variant="outline" size="sm" onClick={() => void onDownload()}>
          <Download data-icon="inline-start" />
          Download file
        </Button>
      ) : null}
    </div>
  )
}

function WorkspaceFilePreview({
  kind,
  title,
  content,
  objectUrl,
  radius,
  onDownload,
}: {
  kind: LemmaDocumentWorkspaceFileFormat
  title: string
  content: string | null
  objectUrl: string | null
  radius: LemmaDocumentWorkspaceRadius
  onDownload: () => void | Promise<void>
}) {
  if (kind === "image" && objectUrl) {
    return (
      <div className="flex min-h-[34rem] items-center justify-center">
        <img src={objectUrl} alt={title} className={cn("max-h-full max-w-full object-contain", radiusClassName(radius, "surface"))} />
      </div>
    )
  }

  if (kind === "pdf" && objectUrl) {
    return <iframe title={title} src={objectUrl} className={cn("h-full min-h-[34rem] w-full border border-border/40 bg-background", radiusClassName(radius, "surface"))} />
  }

  if (kind === "html" && content != null) {
    return <iframe title={title} srcDoc={content} sandbox="" className={cn("h-full min-h-[34rem] w-full border border-border/40 bg-background", radiusClassName(radius, "surface"))} />
  }

  return (
    <div className="flex min-h-[32rem] flex-col items-center justify-center gap-3 text-center">
      <span className={cn("flex size-11 items-center justify-center border border-border/60 bg-muted/40 text-muted-foreground", radiusClassName(radius, "control"))}>
        {kind === "image" ? <Image className="size-5" /> : kind === "pdf" || kind === "html" ? <FileText className="size-5" /> : <File className="size-5" />}
      </span>
      <div>
        <p className="font-medium text-foreground">No inline preview</p>
        <p className="mt-1 text-sm text-muted-foreground">Download the file or open it from another workflow.</p>
      </div>
      <Button variant="outline" size="sm" onClick={() => void onDownload()}>
        <Download data-icon="inline-start" />
        Download file
      </Button>
    </div>
  )
}

function WorkspaceTextFileEditor({
  value,
  onChange,
  editable,
  density,
  radius,
}: {
  value: string
  onChange: (value: string) => void
  editable: boolean
  density: LemmaDocumentWorkspaceDensity
  radius: LemmaDocumentWorkspaceRadius
}) {
  if (!editable) {
    return (
      <pre className={cn("min-h-[34rem] overflow-auto whitespace-pre-wrap border border-border/40 bg-card font-mono text-xs leading-6 text-foreground", radiusClassName(radius, "surface"), textFilePaddingClassName(density))}>
        {value}
      </pre>
    )
  }

  return (
    <Textarea
      value={value}
      onChange={(event) => onChange(event.target.value)}
      className={cn("min-h-[34rem] resize-none border-border/40 bg-card font-mono text-xs leading-6 shadow-none", radiusClassName(radius, "surface"), textFilePaddingClassName(density))}
    />
  )
}

function defineDocumentExtension(placeholder: string, editable: boolean) {
  const base = union(
    defineBasicExtension(),
    definePlaceholder({ placeholder, strategy: "block" }),
  )
  return editable ? base : union(base, defineReadonly())
}

function ProseKitToolbar({
  editor,
  onOpenInsert,
}: {
  editor: LemmaRichDocumentEditor
  onOpenInsert: () => void
}) {
  const deriveToolbarItems = React.useCallback((editor: LemmaRichDocumentEditor) => getToolbarItems(editor), [])
  const items = useEditorDerivedValue<BasicExtension, ToolbarItems>(deriveToolbarItems, { editor })

  return (
    <div className="lemma-prosekit-toolbar flex flex-wrap items-center gap-1 px-3 py-2">
      <ToolbarButton tooltip="Insert" onClick={onOpenInsert}>
        <Plus className="size-4" />
      </ToolbarButton>
      <ToolbarDivider />
      <ToolbarButton tooltip="Undo" disabled={!items.undo?.canExec} onClick={items.undo?.command}>
        <Undo2 className="size-4" />
      </ToolbarButton>
      <ToolbarButton tooltip="Redo" disabled={!items.redo?.canExec} onClick={items.redo?.command}>
        <Redo2 className="size-4" />
      </ToolbarButton>
      <ToolbarDivider />
      <ToolbarButton tooltip="Bold" pressed={items.bold?.isActive} disabled={!items.bold?.canExec} onClick={items.bold?.command}>
        <Bold className="size-4" />
      </ToolbarButton>
      <ToolbarButton tooltip="Italic" pressed={items.italic?.isActive} disabled={!items.italic?.canExec} onClick={items.italic?.command}>
        <Italic className="size-4" />
      </ToolbarButton>
      <ToolbarButton tooltip="Code" pressed={items.code?.isActive} disabled={!items.code?.canExec} onClick={items.code?.command}>
        <Code2 className="size-4" />
      </ToolbarButton>
      <ToolbarDivider />
      <ToolbarButton tooltip="Text" disabled={!items.paragraph?.canExec} onClick={items.paragraph?.command}>
        <Pilcrow className="size-4" />
      </ToolbarButton>
      <ToolbarButton tooltip="Heading 1" pressed={items.heading1?.isActive} disabled={!items.heading1?.canExec} onClick={items.heading1?.command}>
        <Heading1 className="size-4" />
      </ToolbarButton>
      <ToolbarButton tooltip="Heading 2" pressed={items.heading2?.isActive} disabled={!items.heading2?.canExec} onClick={items.heading2?.command}>
        <Heading2 className="size-4" />
      </ToolbarButton>
      <ToolbarButton tooltip="Quote" pressed={items.blockquote?.isActive} disabled={!items.blockquote?.canExec} onClick={items.blockquote?.command}>
        <Quote className="size-4" />
      </ToolbarButton>
      <ToolbarDivider />
      <ToolbarButton tooltip="Bullet list" pressed={items.bulletList?.isActive} disabled={!items.bulletList?.canExec} onClick={items.bulletList?.command}>
        <List className="size-4" />
      </ToolbarButton>
      <ToolbarButton tooltip="Numbered list" pressed={items.orderedList?.isActive} disabled={!items.orderedList?.canExec} onClick={items.orderedList?.command}>
        <ListOrdered className="size-4" />
      </ToolbarButton>
      <ToolbarButton tooltip="Task list" pressed={items.taskList?.isActive} disabled={!items.taskList?.canExec} onClick={items.taskList?.command}>
        <CheckSquare className="size-4" />
      </ToolbarButton>
      <ToolbarDivider />
      <ToolbarButton tooltip="Divider" disabled={!items.horizontalRule?.canExec} onClick={items.horizontalRule?.command}>
        <Minus className="size-4" />
      </ToolbarButton>
      <ToolbarButton tooltip="Table" disabled={!items.table?.canExec} onClick={items.table?.command}>
        <Table2 className="size-4" />
      </ToolbarButton>
    </div>
  )
}

type ToolbarCommand = {
  isActive: boolean
  canExec: boolean
  command: () => void
}

type ToolbarItems = {
  undo?: ToolbarCommand
  redo?: ToolbarCommand
  bold?: ToolbarCommand
  italic?: ToolbarCommand
  code?: ToolbarCommand
  paragraph?: ToolbarCommand
  heading1?: ToolbarCommand
  heading2?: ToolbarCommand
  blockquote?: ToolbarCommand
  bulletList?: ToolbarCommand
  orderedList?: ToolbarCommand
  taskList?: ToolbarCommand
  horizontalRule?: ToolbarCommand
  table?: ToolbarCommand
}

function getToolbarItems(editor: LemmaRichDocumentEditor): ToolbarItems {
  return {
    undo: editor.commands.undo ? {
      isActive: false,
      canExec: editor.commands.undo.canExec(),
      command: () => editor.commands.undo(),
    } : undefined,
    redo: editor.commands.redo ? {
      isActive: false,
      canExec: editor.commands.redo.canExec(),
      command: () => editor.commands.redo(),
    } : undefined,
    bold: editor.commands.toggleBold ? {
      isActive: editor.marks.bold.isActive(),
      canExec: editor.commands.toggleBold.canExec(),
      command: () => editor.commands.toggleBold(),
    } : undefined,
    italic: editor.commands.toggleItalic ? {
      isActive: editor.marks.italic.isActive(),
      canExec: editor.commands.toggleItalic.canExec(),
      command: () => editor.commands.toggleItalic(),
    } : undefined,
    code: editor.commands.toggleCode ? {
      isActive: editor.marks.code.isActive(),
      canExec: editor.commands.toggleCode.canExec(),
      command: () => editor.commands.toggleCode(),
    } : undefined,
    paragraph: editor.commands.setParagraph ? {
      isActive: editor.nodes.paragraph.isActive(),
      canExec: editor.commands.setParagraph.canExec(),
      command: () => editor.commands.setParagraph(),
    } : undefined,
    heading1: editor.commands.toggleHeading ? {
      isActive: editor.nodes.heading.isActive({ level: 1 }),
      canExec: editor.commands.toggleHeading.canExec({ level: 1 }),
      command: () => editor.commands.toggleHeading({ level: 1 }),
    } : undefined,
    heading2: editor.commands.toggleHeading ? {
      isActive: editor.nodes.heading.isActive({ level: 2 }),
      canExec: editor.commands.toggleHeading.canExec({ level: 2 }),
      command: () => editor.commands.toggleHeading({ level: 2 }),
    } : undefined,
    blockquote: editor.commands.toggleBlockquote ? {
      isActive: editor.nodes.blockquote.isActive(),
      canExec: editor.commands.toggleBlockquote.canExec(),
      command: () => editor.commands.toggleBlockquote(),
    } : undefined,
    bulletList: editor.commands.toggleList ? {
      isActive: editor.nodes.list.isActive({ kind: "bullet" }),
      canExec: editor.commands.toggleList.canExec({ kind: "bullet" }),
      command: () => editor.commands.toggleList({ kind: "bullet" }),
    } : undefined,
    orderedList: editor.commands.toggleList ? {
      isActive: editor.nodes.list.isActive({ kind: "ordered" }),
      canExec: editor.commands.toggleList.canExec({ kind: "ordered" }),
      command: () => editor.commands.toggleList({ kind: "ordered" }),
    } : undefined,
    taskList: editor.commands.toggleList ? {
      isActive: editor.nodes.list.isActive({ kind: "task" }),
      canExec: editor.commands.toggleList.canExec({ kind: "task" }),
      command: () => editor.commands.toggleList({ kind: "task" }),
    } : undefined,
    horizontalRule: editor.commands.insertHorizontalRule ? {
      isActive: false,
      canExec: editor.commands.insertHorizontalRule.canExec(),
      command: () => editor.commands.insertHorizontalRule(),
    } : undefined,
    table: editor.commands.insertTable ? {
      isActive: editor.nodes.table.isActive(),
      canExec: editor.commands.insertTable.canExec({ row: 3, col: 3, header: true }),
      command: () => insertSeededTable(editor),
    } : undefined,
  }
}

function ToolbarButton({
  children,
  disabled,
  onClick,
  pressed,
  tooltip,
}: {
  children: React.ReactNode
  disabled?: boolean
  onClick?: () => void
  pressed?: boolean
  tooltip: string
}) {
  return (
    <button
      type="button"
      title={tooltip}
      aria-label={tooltip}
      aria-pressed={pressed}
      disabled={disabled}
      onClick={onClick}
      onMouseDown={(event) => event.preventDefault()}
      className={cn(
        "inline-flex size-8 items-center justify-center rounded-md text-muted-foreground transition-colors hover:bg-muted hover:text-foreground disabled:pointer-events-none disabled:opacity-35",
        pressed ? "bg-muted text-foreground" : null,
      )}
    >
      {children}
    </button>
  )
}

function ToolbarDivider() {
  return <span className="mx-1 h-5 w-px bg-border" />
}

function SlashMenu({
  commands,
  onSelect,
}: {
  commands: LemmaDocumentWorkspaceCommand[]
  onSelect: (command: LemmaDocumentWorkspaceCommand) => void
}) {
  useEditor<BasicExtension>()

  return (
    <AutocompletePopover
      regex={slashRegex}
      className="relative z-50 block max-h-96 min-w-72 select-none overflow-auto whitespace-nowrap rounded-lg border border-border bg-popover p-1 text-popover-foreground shadow-xl [&:not([data-state])]:hidden"
    >
      <AutocompleteList>
        {commands.map((command) => (
          <AutocompleteItem
            key={command.id}
            onSelect={() => onSelect(command)}
            className="relative flex cursor-default items-start gap-3 rounded-md px-3 py-2 outline-none data-focused:bg-muted data-highlighted:bg-muted"
          >
            <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center rounded-md border border-border bg-background text-muted-foreground">
              {command.icon ?? <Plus className="size-4" />}
            </span>
            <span className="min-w-0">
              <span className="block text-sm font-medium text-foreground">{command.label}</span>
              {command.description ? <span className="mt-0.5 block text-xs text-muted-foreground">{command.description}</span> : null}
            </span>
          </AutocompleteItem>
        ))}
        <AutocompleteEmpty className="relative flex cursor-default items-center justify-between rounded-md px-3 py-2 text-sm text-muted-foreground outline-none data-focused:bg-muted data-highlighted:bg-muted">
          <span>No blocks found</span>
        </AutocompleteEmpty>
      </AutocompleteList>
    </AutocompletePopover>
  )
}

function DocumentTableHandle() {
  const state = useEditorDerivedValue<BasicExtension, ReturnType<typeof getTableHandleState>>(getTableHandleState)

  return (
    <TableHandleRoot className="contents">
      <TableHandleDragPreview />
      <TableHandleDropIndicator />
      <TableHandleRowRoot className="contents">
        <TableHandleRowTrigger className="lemma-table-handle-trigger lemma-table-handle-row-trigger" />
        <TableHandlePopoverContent className="lemma-table-handle-popover">
          {state.addTableRowAbove.canExec ? (
            <TableHandlePopoverItem className="lemma-table-handle-item" onSelect={state.addTableRowAbove.command}>
              <Rows3 className="size-3.5" />
              <span>Insert above</span>
            </TableHandlePopoverItem>
          ) : null}
          {state.addTableRowBelow.canExec ? (
            <TableHandlePopoverItem className="lemma-table-handle-item" onSelect={state.addTableRowBelow.command}>
              <Plus className="size-3.5" />
              <span>Insert below</span>
            </TableHandlePopoverItem>
          ) : null}
          {state.deleteCellSelection.canExec ? (
            <TableHandlePopoverItem className="lemma-table-handle-item" onSelect={state.deleteCellSelection.command}>
              <span>Clear contents</span>
            </TableHandlePopoverItem>
          ) : null}
          {state.deleteTableRow.canExec ? (
            <TableHandlePopoverItem className="lemma-table-handle-item" onSelect={state.deleteTableRow.command}>
              <Trash2 className="size-3.5" />
              <span>Delete row</span>
            </TableHandlePopoverItem>
          ) : null}
          {state.deleteTable.canExec ? (
            <TableHandlePopoverItem className="lemma-table-handle-item lemma-table-handle-item-danger" onSelect={state.deleteTable.command}>
              <Trash2 className="size-3.5" />
              <span>Delete table</span>
            </TableHandlePopoverItem>
          ) : null}
        </TableHandlePopoverContent>
      </TableHandleRowRoot>
      <TableHandleColumnRoot className="contents">
        <TableHandleColumnTrigger className="lemma-table-handle-trigger lemma-table-handle-col-trigger" />
        <TableHandlePopoverContent className="lemma-table-handle-popover">
          {state.addTableColumnBefore.canExec ? (
            <TableHandlePopoverItem className="lemma-table-handle-item" onSelect={state.addTableColumnBefore.command}>
              <Plus className="size-3.5" />
              <span>Insert left</span>
            </TableHandlePopoverItem>
          ) : null}
          {state.addTableColumnAfter.canExec ? (
            <TableHandlePopoverItem className="lemma-table-handle-item" onSelect={state.addTableColumnAfter.command}>
              <Plus className="size-3.5" />
              <span>Insert right</span>
            </TableHandlePopoverItem>
          ) : null}
          {state.deleteCellSelection.canExec ? (
            <TableHandlePopoverItem className="lemma-table-handle-item" onSelect={state.deleteCellSelection.command}>
              <span>Clear contents</span>
            </TableHandlePopoverItem>
          ) : null}
          {state.deleteTableColumn.canExec ? (
            <TableHandlePopoverItem className="lemma-table-handle-item" onSelect={state.deleteTableColumn.command}>
              <Trash2 className="size-3.5" />
              <span>Delete column</span>
            </TableHandlePopoverItem>
          ) : null}
          {state.deleteTable.canExec ? (
            <TableHandlePopoverItem className="lemma-table-handle-item lemma-table-handle-item-danger" onSelect={state.deleteTable.command}>
              <Trash2 className="size-3.5" />
              <span>Delete table</span>
            </TableHandlePopoverItem>
          ) : null}
        </TableHandlePopoverContent>
      </TableHandleColumnRoot>
    </TableHandleRoot>
  )
}

function Grip({ className }: { className?: string }) {
  return (
    <svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" className={className}>
      <circle cx="9" cy="5" r="1" /><circle cx="9" cy="12" r="1" /><circle cx="9" cy="19" r="1" />
      <circle cx="15" cy="5" r="1" /><circle cx="15" cy="12" r="1" /><circle cx="15" cy="19" r="1" />
    </svg>
  )
}

function getTableHandleState(editor: LemmaRichDocumentEditor) {
  return {
    addTableColumnBefore: {
      canExec: editor.commands.addTableColumnBefore?.canExec() ?? false,
      command: () => editor.commands.addTableColumnBefore?.(),
    },
    addTableColumnAfter: {
      canExec: editor.commands.addTableColumnAfter?.canExec() ?? false,
      command: () => editor.commands.addTableColumnAfter?.(),
    },
    addTableRowAbove: {
      canExec: editor.commands.addTableRowAbove?.canExec() ?? false,
      command: () => editor.commands.addTableRowAbove?.(),
    },
    addTableRowBelow: {
      canExec: editor.commands.addTableRowBelow?.canExec() ?? false,
      command: () => editor.commands.addTableRowBelow?.(),
    },
    deleteCellSelection: {
      canExec: editor.commands.deleteCellSelection?.canExec() ?? false,
      command: () => editor.commands.deleteCellSelection?.(),
    },
    deleteTableColumn: {
      canExec: editor.commands.deleteTableColumn?.canExec() ?? false,
      command: () => editor.commands.deleteTableColumn?.(),
    },
    deleteTableRow: {
      canExec: editor.commands.deleteTableRow?.canExec() ?? false,
      command: () => editor.commands.deleteTableRow?.(),
    },
    deleteTable: {
      canExec: editor.commands.deleteTable?.canExec() ?? false,
      command: () => editor.commands.deleteTable?.(),
    },
  }
}

function CommandMenu({
  query,
  onQueryChange,
  commands,
  radius,
  density,
  onClose,
  onSelect,
}: {
  query: string
  onQueryChange: (value: string) => void
  commands: LemmaDocumentWorkspaceCommand[]
  radius: LemmaDocumentWorkspaceRadius
  density: LemmaDocumentWorkspaceDensity
  onClose: () => void
  onSelect: (command: LemmaDocumentWorkspaceCommand) => void
}) {
  return (
    <div className={cn("border-t border-border/60 bg-popover", radiusClassName(radius, "surface"))}>
      <div className="flex items-center gap-2 border-b border-border/60 px-3 py-2">
        <Search className="size-4 text-muted-foreground" />
        <Input
          value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          placeholder="Find a block or Lemma action..."
          autoFocus
          className="h-8 border-0 bg-transparent px-0 shadow-none focus-visible:ring-0"
        />
        <Button type="button" variant="ghost" size="sm" onClick={onClose} aria-label="Close command menu">
          <X className="size-4" />
        </Button>
      </div>
      <div className={cn("grid max-h-80 gap-1 overflow-auto", commandPaddingClassName(density))}>
        {commands.length > 0 ? (
          commands.map((command) => (
            <button
              key={command.id}
              type="button"
              disabled={command.disabled}
              onClick={() => onSelect(command)}
              className={cn("flex items-start gap-3 p-2 text-left transition-colors hover:bg-muted/55 disabled:pointer-events-none disabled:opacity-50", radiusClassName(radius, "control"))}
            >
              <span className="mt-0.5 flex size-7 shrink-0 items-center justify-center border border-border bg-background text-muted-foreground">
                {command.icon ?? <Plus className="size-4" />}
              </span>
              <span className="min-w-0">
                <span className="block text-sm font-medium text-foreground">{command.label}</span>
                {command.description ? <span className="mt-0.5 block text-xs text-muted-foreground">{command.description}</span> : null}
              </span>
            </button>
          ))
        ) : (
          <p className="p-4 text-sm text-muted-foreground">No matching blocks.</p>
        )}
      </div>
    </div>
  )
}

function InspectorCard({
  title,
  icon,
  children,
  radius,
}: {
  title: React.ReactNode
  icon?: React.ReactNode
  children: React.ReactNode
  radius: LemmaDocumentWorkspaceRadius
}) {
  return (
    <section className={cn("border border-border bg-background p-4", radiusClassName(radius, "surface"))}>
      <div className="mb-3 flex items-center gap-2 text-sm font-medium text-foreground">
        <span className="text-muted-foreground">{icon ?? <MessageSquare className="size-4" />}</span>
        <span>{title}</span>
      </div>
      {children}
    </section>
  )
}

function InspectorRow({ label, value }: { label: React.ReactNode; value: React.ReactNode }) {
  return (
    <div className="flex items-start justify-between gap-3 py-1 text-sm">
      <span className="text-muted-foreground">{label}</span>
      <span className="text-right text-foreground">{value}</span>
    </div>
  )
}

function ReferenceList({ items, radius }: { items: LemmaDocumentWorkspaceReference[]; radius: LemmaDocumentWorkspaceRadius }) {
  return (
    <div className="space-y-2">
      {items.map((item) => {
        const content = (
          <div className="min-w-0">
            <p className="truncate text-sm font-medium text-foreground">{item.title}</p>
            {item.description ? <p className="mt-1 text-sm text-muted-foreground">{item.description}</p> : null}
            {item.meta ? <p className="mt-1 text-xs text-muted-foreground">{item.meta}</p> : null}
          </div>
        )

        if (item.onSelect) {
          return (
            <button key={item.id} type="button" onClick={item.onSelect} className={cn("w-full border border-border bg-muted/20 p-3 text-left transition-colors hover:bg-muted/45", radiusClassName(radius, "surface"))}>
              {content}
            </button>
          )
        }

        if (item.href) {
          return (
            <a key={item.id} href={item.href} className={cn("block border border-border bg-muted/20 p-3 transition-colors hover:bg-muted/45", radiusClassName(radius, "surface"))}>
              {content}
            </a>
          )
        }

        return (
          <div key={item.id} className={cn("border border-border bg-muted/20 p-3", radiusClassName(radius, "surface"))}>
            {content}
          </div>
        )
      })}
    </div>
  )
}

function insertBlockText(editor: LemmaRichDocumentEditor, text: string, kind: "paragraph" | "blockquote") {
  prepareForBlockInsert(editor)
  const paragraph = editor.nodes.paragraph(text)
  if (kind === "blockquote") {
    editor.commands.insertNode({ node: editor.nodes.blockquote(paragraph) })
  } else {
    editor.commands.insertNode({ node: paragraph })
  }
  editor.commands.insertDefaultBlock()
}

function insertSeededTable(editor: LemmaRichDocumentEditor) {
  prepareForBlockInsert(editor)
  const paragraph = editor.nodes.paragraph
  const headerCell = (text: string) => editor.nodes.tableHeaderCell({}, paragraph(text))
  const cell = (text?: string) => editor.nodes.tableCell({}, text ? paragraph(text) : paragraph())
  editor.commands.insertNode({
    node: editor.nodes.table(
      editor.nodes.tableRow(
        headerCell("Column 1"),
        headerCell("Column 2"),
        headerCell("Column 3"),
      ),
      editor.nodes.tableRow(
        cell("Value"),
        cell("Value"),
        cell("Value"),
      ),
      editor.nodes.tableRow(
        cell(),
        cell(),
        cell(),
      ),
    ),
  })
  editor.commands.insertDefaultBlock()
}

function insertFileBlock(editor: LemmaRichDocumentEditor, block: LemmaDocumentWorkspaceFileBlock) {
  insertEmbedBlock(editor, [
    paragraphNode(editor, "File"),
    paragraphNode(editor, block.title.trim() || fileNameFromPath(block.path)),
    paragraphNode(editor, block.path.trim()),
    optionalParagraphNode(editor, block.description),
    optionalParagraphNode(editor, block.meta),
  ])
}

function insertRecordsBlock(editor: LemmaRichDocumentEditor, block: LemmaDocumentWorkspaceRecordsBlock) {
  const filters = normalizeRecordsFilters(block.filters)
  const columns = normalizeStringList(block.columns)
  const sorts = normalizeStringList(block.sorts)
  const title = block.title?.trim() || `Filtered ${block.table.trim() || "records"}`

  insertEmbedBlock(editor, [
    paragraphNode(editor, "Records"),
    optionalParagraphNode(editor, title),
    optionalParagraphNode(editor, `Table: ${block.table.trim() || "records"}`),
    optionalParagraphNode(editor, filters.length > 0 ? `Filters: ${filters.join(" | ")}` : undefined),
    optionalParagraphNode(editor, columns.length > 0 ? `Columns: ${columns.join(", ")}` : undefined),
    optionalParagraphNode(editor, sorts.length > 0 ? `Sort: ${sorts.join(", ")}` : undefined),
    optionalParagraphNode(editor, block.description),
  ])
}

function insertEmbedBlock(editor: LemmaRichDocumentEditor, blocks: Array<ReturnType<LemmaRichDocumentEditor["nodes"]["paragraph"]> | null | undefined>) {
  const content = blocks.filter((block): block is NonNullable<typeof block> => Boolean(block))
  if (content.length === 0) return
  prepareForBlockInsert(editor)
  editor.commands.insertNode({
    node: editor.nodes.blockquote(...content),
  })
  editor.commands.insertDefaultBlock()
}

function prepareForBlockInsert(editor: LemmaRichDocumentEditor) {
  if (editor.nodes.list.isActive()) {
    editor.commands.unwrapList()
  }
}

function optionalParagraphNode(editor: LemmaRichDocumentEditor, text: string | null | undefined) {
  const normalized = text?.trim()
  return normalized ? paragraphNode(editor, normalized) : null
}

function paragraphNode(editor: LemmaRichDocumentEditor, text: string) {
  return editor.nodes.paragraph(text)
}

function normalizeStringList(values: string[] | undefined): string[] {
  return values?.map((value) => value.trim()).filter(Boolean) ?? []
}

function fileNameFromPath(path: string): string {
  const normalized = path.replace(/\\/g, "/")
  const parts = normalized.split("/").filter(Boolean)
  return parts[parts.length - 1] ?? normalized
}

function buildWorkspaceFilePath(directoryPath?: string, fileName?: string): string {
  const directory = normalizePath(directoryPath)
  const name = normalizeWorkspaceFileName(fileName || "untitled-document.lemma-doc.json")
  return `${directory === "/" ? "" : directory}/${name}` || `/${name}`
}

function normalizeWorkspaceFileName(name: string): string {
  const trimmed = name.trim().replace(/\\/g, "/").split("/").filter(Boolean).pop() ?? ""
  return trimmed || "untitled-document.lemma-doc.json"
}

function documentFileNameFromTitle(title: string): string {
  const slug = title
    .trim()
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
  return `${slug || "untitled-document"}.lemma-doc.json`
}

function stripWorkspaceDocumentExtension(name: string): string {
  return name.toLowerCase().endsWith(".lemma-doc.json") ? name.slice(0, -".lemma-doc.json".length) : name
}

function humanizeWorkspaceFileName(path: string): string {
  const base = stripWorkspaceDocumentExtension(fileNameFromPath(path)).replace(/\.[^.]+$/, "")
  const spaced = base.replace(/[-_]+/g, " ").trim()
  if (!spaced) return "Untitled document"
  return spaced.replace(/\b\w/g, (value) => value.toUpperCase())
}

function displayWorkspacePath(path: string): string {
  const normalized = path.trim().replace(/\\/g, "/")
  const parts = normalized.split("/")
  for (let index = parts.length - 1; index >= 0; index -= 1) {
    if (!parts[index]) continue
    parts[index] = stripWorkspaceDocumentExtension(parts[index])
    break
  }
  return parts.join("/")
}

function downloadFileNameFromPath(path: string): string {
  const name = fileNameFromPath(path)
  if (!name.toLowerCase().endsWith(".lemma-doc.json")) return name
  return `${stripWorkspaceDocumentExtension(name)}.json`
}

function extensionFromPath(path: string): string {
  const name = fileNameFromPath(path)
  const index = name.lastIndexOf(".")
  if (index < 0) return ""
  return name.slice(index + 1).toLowerCase()
}

function inferWorkspaceFileFormat(
  requested: LemmaDocumentWorkspaceFileFormat,
  file: FileResponse | null | undefined,
  path: string,
): LemmaDocumentWorkspaceFileFormat {
  if (requested !== "auto") return requested
  const mime = file?.mime_type?.toLowerCase() ?? ""
  const extension = extensionFromPath(path)
  const name = fileNameFromPath(path).toLowerCase()
  if (name.endsWith(".lemma-doc.json")) return "structured"
  if (mime.startsWith("image/") || /^(jpg|jpeg|png|gif|webp|svg|bmp|ico|tiff|avif)$/.test(extension)) return "image"
  if (mime === "application/pdf" || extension === "pdf") return "pdf"
  if (extension === "md" || extension === "markdown" || extension === "mdx") return "markdown"
  if (isWorkspaceTextLikeMime(mime) || isWorkspaceTextLikeFile(extension, name)) return "text"
  if (/^(doc|docx|ppt|pptx|xls|xlsx|pages|rtf)$/.test(extension)) return "html"
  return "download"
}

function isWorkspaceTextLikeMime(mime: string): boolean {
  return mime.startsWith("text/") || /(json|xml|yaml|toml|javascript|typescript|shellscript|x-sh|graphql)/.test(mime)
}

function isWorkspaceTextLikeFile(extension: string, name: string): boolean {
  if (name === "dockerfile" || name === "makefile") return true
  return /^(txt|text|csv|log|json|jsonc|xml|yaml|yml|toml|ini|conf|env|js|mjs|cjs|jsx|ts|mts|cts|tsx|css|scss|sass|less|html|htm|py|rb|go|rs|java|kt|swift|c|h|cc|cpp|hpp|cs|php|sh|bash|zsh|fish|sql|graphql|gql)$/.test(extension)
}

function inferWorkspaceTextMimeType(mimeType: string | null | undefined, path: string, kind: LemmaDocumentWorkspaceFileFormat): string {
  if (mimeType?.trim()) return mimeType
  const extension = extensionFromPath(path)
  if (kind === "markdown") return "text/markdown;charset=utf-8"
  if (extension === "json" || extension === "jsonc") return "application/json;charset=utf-8"
  if (extension === "yaml" || extension === "yml") return "application/yaml;charset=utf-8"
  if (extension === "html" || extension === "htm") return "text/html;charset=utf-8"
  if (extension === "csv") return "text/csv;charset=utf-8"
  return "text/plain;charset=utf-8"
}

function buildWorkspaceDocumentBody({
  title,
  summary,
  content,
}: {
  title: string
  summary: string
  content: LemmaDocumentNode
}): string {
  return JSON.stringify(
    {
      type: "lemma.document",
      version: 1,
      title: title.trim() || "Untitled document",
      summary: summary.trim(),
      content,
    },
    null,
    2,
  )
}

function parsePersistedWorkspaceDocumentBody(body: string): {
  title: string | null
  summary: string | null
  content: LemmaDocumentNode
} | null {
  const trimmed = body.trim()
  if (!trimmed.startsWith("{")) return null
  try {
    const parsed = JSON.parse(trimmed) as {
      type?: string
      title?: unknown
      summary?: unknown
      content?: unknown
    }
    const content = parsed.type === "doc"
      ? sanitizeDocument(parsed)
      : sanitizeDocument(parsed.content)
    if (parsed.type !== "lemma.document" && parsed.type !== "doc") return null
    return {
      title: typeof parsed.title === "string" ? parsed.title : null,
      summary: typeof parsed.summary === "string" ? parsed.summary : null,
      content,
    }
  } catch {
    return null
  }
}

function normalizeRecordsFilters(filters: Array<string | LemmaDocumentWorkspaceRecordsFilter> | undefined): string[] {
  return filters?.map((filter) => {
    if (typeof filter === "string") return filter.trim()
    const label = filter.label?.trim()
    const value = filter.value.trim()
    if (!value) return ""
    return label ? `${label}: ${value}` : value
  }).filter(Boolean) ?? []
}

function normalizeDocumentContent(value: LemmaDocumentNode | string | null | undefined): LemmaDocumentNode {
  if (!value) return emptyDoc()
  if (typeof value !== "string") return sanitizeDocument(value)

  const trimmed = value.trim()
  if (!trimmed) return emptyDoc()
  if (trimmed.startsWith("{")) {
    try {
      const parsed = JSON.parse(trimmed) as LemmaDocumentNode
      return sanitizeDocument(parsed)
    } catch {
      return markdownLikeToDoc(value)
    }
  }
  return markdownLikeToDoc(value)
}

function sanitizeDocument(value: unknown): LemmaDocumentNode {
  if (!isNodeLike(value)) return emptyDoc()
  if (value.type !== "doc") {
    const block = sanitizeBlock(value)
    return { type: "doc", content: block ? [block] : [{ type: "paragraph" }] }
  }
  const content = value.content?.map(sanitizeBlock).filter(isNodeLike) ?? []
  return {
    type: "doc",
    content: content.length > 0 ? content : [{ type: "paragraph" }],
  }
}

function sanitizeBlock(node: LemmaDocumentNode): LemmaDocumentNode | null {
  if (!isNodeLike(node)) return null

  if (node.type === "paragraph") {
    return { type: "paragraph", content: sanitizeInlineContent(node.content) }
  }

  if (node.type === "heading") {
    const level = Number(node.attrs?.level ?? 1)
    return {
      type: "heading",
      attrs: { level: Math.min(6, Math.max(1, Number.isFinite(level) ? level : 1)) },
      content: sanitizeInlineContent(node.content),
    }
  }

  if (node.type === "blockquote") {
    const content = node.content?.map(sanitizeBlock).filter(isNodeLike) ?? []
    return { type: "blockquote", content: content.length > 0 ? content : [{ type: "paragraph" }] }
  }

  if (node.type === "codeBlock") {
    return {
      type: "codeBlock",
      attrs: node.attrs?.language ? { language: String(node.attrs.language) } : undefined,
      content: sanitizeTextOnly(node.content),
    }
  }

  if (node.type === "horizontalRule") {
    return { type: "horizontalRule" }
  }

  if (node.type === "list") {
    const attrs = sanitizeListAttrs(node.attrs)
    const content = node.content?.map(sanitizeBlock).filter(isNodeLike) ?? []
    if (content.length === 0 || content.every(isBlankBlock)) return null
    return {
      type: "list",
      attrs,
      content,
    }
  }

  if (node.type === "bulletList" || node.type === "orderedList" || node.type === "taskList") {
    const items = node.content?.map(legacyListItemToTextBlock).filter(isNodeLike) ?? []
    if (items.length === 0) return null
    return items.length === 1 ? items[0] : { type: "blockquote", content: items }
  }

  if (node.type === "table") {
    const rows = node.content?.map(sanitizeBlock).filter(isTableRow) ?? []
    return rows.length > 0 ? { type: "table", content: rows } : null
  }

  if (node.type === "tableRow") {
    const cells = node.content?.map(sanitizeBlock).filter(isTableCell) ?? []
    return { type: "tableRow", content: cells.length > 0 ? cells : [emptyTableCell()] }
  }

  if (node.type === "tableCell" || node.type === "tableHeaderCell" || node.type === "tableHeader") {
    const content = node.content?.map(sanitizeBlock).filter(isNodeLike) ?? []
    return {
      type: node.type === "tableHeader" ? "tableHeaderCell" : node.type,
      attrs: sanitizeCellAttrs(node.attrs),
      content: content.length > 0 ? content : [{ type: "paragraph" }],
    }
  }

  if (node.type === "image") {
    const src = typeof node.attrs?.src === "string" ? node.attrs.src : ""
    if (!src) return null
    return { type: "image", attrs: { ...node.attrs, src } }
  }

  if (node.type === "hardBreak") {
    return { type: "paragraph" }
  }

  const fallbackText = documentTextFromContent(node)
  return fallbackText ? paragraphFromText(fallbackText) : null
}

function sanitizeInlineContent(content: LemmaDocumentNode[] | undefined) {
  const result = content?.flatMap(sanitizeInlineNode).filter(isNodeLike) ?? []
  return result.length > 0 ? result : undefined
}

function sanitizeInlineNode(node: LemmaDocumentNode): LemmaDocumentNode[] {
  if (!isNodeLike(node)) return []
  if (node.type === "text") {
    return typeof node.text === "string" ? [{
      type: "text",
      text: node.text,
      marks: sanitizeMarks(node.marks),
    }] : []
  }
  if (node.type === "hardBreak") return [{ type: "hardBreak" }]
  const text = documentTextFromContent(node)
  return text ? [{ type: "text", text }] : []
}

function sanitizeTextOnly(content: LemmaDocumentNode[] | undefined) {
  const text = content?.map(documentTextFromContent).join("\n") ?? ""
  return text ? [{ type: "text", text }] : undefined
}

function sanitizeMarks(marks: LemmaDocumentNode["marks"]) {
  const allowed = new Set(["bold", "italic", "underline", "strike", "code", "link"])
  const next = marks?.filter((mark) => allowed.has(mark.type)).map((mark) => ({
    type: mark.type,
    attrs: mark.attrs,
  }))
  return next && next.length > 0 ? next : undefined
}

function sanitizeListAttrs(attrs: LemmaDocumentNode["attrs"]): Record<string, unknown> {
  const kind = attrs?.kind === "ordered" || attrs?.kind === "task" || attrs?.kind === "toggle" ? attrs.kind : "bullet"
  return {
    kind,
    order: typeof attrs?.order === "number" ? attrs.order : undefined,
    checked: typeof attrs?.checked === "boolean" ? attrs.checked : undefined,
    collapsed: typeof attrs?.collapsed === "boolean" ? attrs.collapsed : undefined,
  }
}

function sanitizeCellAttrs(attrs: LemmaDocumentNode["attrs"]): Record<string, unknown> | undefined {
  if (!attrs) return undefined
  const result: Record<string, unknown> = {}
  if (typeof attrs.colspan === "number") result.colspan = attrs.colspan
  if (typeof attrs.rowspan === "number") result.rowspan = attrs.rowspan
  if (Array.isArray(attrs.colwidth)) result.colwidth = attrs.colwidth
  return Object.keys(result).length > 0 ? result : undefined
}

function legacyListItemToTextBlock(node: LemmaDocumentNode): LemmaDocumentNode | null {
  if (!isNodeLike(node)) return null
  const text = documentTextFromContent(node)
  return text ? paragraphFromText(text) : null
}

function emptyTableCell(): LemmaDocumentNode {
  return {
    type: "tableCell",
    content: [{ type: "paragraph" }],
  }
}

function emptyDoc(): LemmaDocumentNode {
  return {
    type: "doc",
    content: [{ type: "paragraph" }],
  }
}

function paragraphFromText(text: string): LemmaDocumentNode {
  return {
    type: "paragraph",
    content: text ? [{ type: "text", text }] : undefined,
  }
}

function markdownLikeToDoc(value: string): LemmaDocumentNode {
  const content: LemmaDocumentNode[] = []
  const lines = value.split("\n")
  for (const rawLine of lines) {
    const line = rawLine.trim()
    if (!line) {
      content.push({ type: "paragraph" })
      continue
    }

    const heading = /^(#{1,6})\s+(.+)$/.exec(line)
    if (heading) {
      content.push({
        type: "heading",
        attrs: { level: heading[1].length },
        content: [{ type: "text", text: heading[2] }],
      })
      continue
    }

    const task = /^[-*]\s+\[([ xX])\]\s+(.+)$/.exec(line)
    if (task) {
      content.push({
        type: "list",
        attrs: { kind: "task", checked: task[1].toLowerCase() === "x" },
        content: [paragraphFromText(task[2])],
      })
      continue
    }

    const ordered = /^\d+\.\s+(.+)$/.exec(line)
    if (ordered) {
      content.push({
        type: "list",
        attrs: { kind: "ordered" },
        content: [paragraphFromText(ordered[1])],
      })
      continue
    }

    if (/^[-*]\s+/.test(line)) {
      content.push({
        type: "list",
        attrs: { kind: "bullet" },
        content: [paragraphFromText(line.replace(/^[-*]\s+/, ""))],
      })
      continue
    }

    if (/^>\s+/.test(line)) {
      content.push({
        type: "blockquote",
        content: [paragraphFromText(line.replace(/^>\s+/, ""))],
      })
      continue
    }

    content.push(paragraphFromText(line))
  }

  return {
    type: "doc",
    content: content.length > 0 ? content : [{ type: "paragraph" }],
  }
}

function documentTextFromContent(content: LemmaDocumentNode | null | undefined): string {
  if (!content) return ""
  const parts: string[] = []
  const visit = (node: LemmaDocumentNode) => {
    if (typeof node.text === "string") parts.push(node.text)
    for (const key of ["title", "path", "prompt", "src"]) {
      const value = node.attrs?.[key]
      if (typeof value === "string") parts.push(value)
    }
    node.content?.forEach(visit)
  }
  visit(content)
  return parts.join(" ").replace(/\s+/g, " ").trim()
}

function getDocumentHTML(editor: LemmaRichDocumentEditor): string {
  try {
    if (typeof document === "undefined") return ""
    return editor.getDocHTML({ document })
  } catch {
    return ""
  }
}

function summarizeDocument(content: LemmaDocumentNode) {
  return documentStats(documentTextFromContent(content))
}

function documentStats(text: string) {
  const normalized = text.trim()
  return {
    words: normalized ? normalized.split(/\s+/).length : 0,
    characters: normalized.length,
  }
}

function isNodeLike(value: unknown): value is LemmaDocumentNode {
  return Boolean(value && typeof value === "object" && typeof (value as LemmaDocumentNode).type === "string")
}

function isTableRow(value: LemmaDocumentNode | null): value is LemmaDocumentNode {
  return isNodeLike(value) && value.type === "tableRow"
}

function isTableCell(value: LemmaDocumentNode | null): value is LemmaDocumentNode {
  return isNodeLike(value) && (value.type === "tableCell" || value.type === "tableHeaderCell")
}

function isBlankBlock(value: LemmaDocumentNode) {
  return !documentTextFromContent(value)
}

function safeStringify(value: unknown) {
  try {
    return JSON.stringify(value)
  } catch {
    return ""
  }
}

function plainNodeText(value: React.ReactNode) {
  if (value == null || typeof value === "boolean") return ""
  if (typeof value === "string" || typeof value === "number") return String(value)
  return ""
}

function normalizePath(path: string | null | undefined): string {
  const trimmed = path?.trim() || "/"
  const normalized = trimmed.startsWith("/") ? trimmed : `/${trimmed}`
  const collapsed = normalized.replace(/\/+/g, "/")
  return collapsed.length > 1 && collapsed.endsWith("/") ? collapsed.slice(0, -1) : collapsed
}

function flattenDirectoryTree(tree: DatastoreDirectoryTreeNode | null | undefined): Array<{ path: string; name: string; depth: number }> {
  if (!tree) return []
  const result: Array<{ path: string; name: string; depth: number }> = []
  const visit = (node: DatastoreDirectoryTreeNode, depth: number) => {
    if (node.kind === "directory") {
      result.push({
        path: normalizePath(node.path),
        name: node.name || fileNameFromPath(node.path),
        depth,
      })
    }
    node.children?.forEach((child) => visit(child, depth + 1))
  }
  visit(tree, 0)
  return result
}

function displayFileLabel(file: FileResponse): string {
  return file.name?.trim() || fileNameFromPath(file.path)
}

function isDirectoryLike(file: FileResponse): boolean {
  const kind = file.kind?.toLowerCase() || ""
  return kind.includes("directory") || kind.includes("folder")
}

function formatBytes(value?: number): string {
  if (!value || value <= 0) return "0 B"
  const units = ["B", "KB", "MB", "GB", "TB"]
  let size = value
  let unitIndex = 0
  while (size >= 1024 && unitIndex < units.length - 1) {
    size /= 1024
    unitIndex += 1
  }
  return `${size >= 10 || unitIndex === 0 ? Math.round(size) : size.toFixed(1)} ${units[unitIndex]}`
}

function formatWorkspaceDate(value: string): string {
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value
  return new Intl.DateTimeFormat(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  }).format(date)
}

function formatFileMeta(file: FileResponse): string {
  const parts = [
    file.kind || undefined,
    file.updated_at ? `Updated ${new Date(file.updated_at).toLocaleDateString()}` : undefined,
    file.size_bytes ? formatBytes(file.size_bytes) : undefined,
  ].filter(Boolean)
  return parts.join(" | ")
}

function parseLineList(value: string): string[] {
  return value
    .split("\n")
    .map((entry) => entry.trim())
    .filter(Boolean)
}

function intentLabel(intent: LemmaDocumentWorkspaceIntent) {
  if (intent === "create") return "New document"
  if (intent === "read") return "Reading"
  return "Editing"
}

function saveStateLabel(state: LemmaDocumentWorkspaceSaveState, intent: LemmaDocumentWorkspaceIntent) {
  if (state === "saving") return intent === "create" ? "Creating" : "Saving"
  if (state === "dirty") return "Unsaved"
  if (state === "error") return "Needs attention"
  return intent === "create" ? "Ready" : "Saved"
}

function surfaceClassName(
  appearance: LemmaDocumentWorkspaceAppearance,
  radius: LemmaDocumentWorkspaceRadius,
  mode: LemmaDocumentWorkspaceMode,
) {
  if (mode === "modal") return "h-full border-0 bg-background shadow-none"
  return cn(
    radiusClassName(radius, "surface"),
    appearance === "minimal" ? "border-0 bg-transparent shadow-none" : null,
    appearance === "borderless" ? "border-0 bg-transparent shadow-none" : null,
    appearance === "contained" ? "border border-border bg-card shadow-sm" : null,
    appearance === "default" ? "border border-border bg-background shadow-sm" : null,
  )
}

function overlayClassName(appearance: LemmaDocumentWorkspaceAppearance, radius: LemmaDocumentWorkspaceRadius) {
  return cn(
    radiusClassName(radius, "overlay"),
    appearance === "minimal" ? "border-0 bg-background shadow-none" : null,
    appearance === "borderless" ? "border-0 bg-background shadow-xl" : null,
    appearance === "contained" ? "border border-border bg-card shadow-xl" : null,
    appearance === "default" ? "border border-border bg-background shadow-xl" : null,
  )
}

function chromePaddingClassName(density: LemmaDocumentWorkspaceDensity) {
  if (density === "compact") return "px-4 py-2.5"
  if (density === "spacious") return "px-6 py-4"
  return "px-5 py-3"
}

function contentPaddingClassName(density: LemmaDocumentWorkspaceDensity) {
  if (density === "compact") return "px-4 pt-6 pb-10 md:px-8"
  if (density === "spacious") return "px-6 pt-10 pb-16 md:px-16"
  return "px-5 pt-8 pb-12 md:px-12"
}

function inlineTitleClassName(density: LemmaDocumentWorkspaceDensity) {
  if (density === "compact") return "text-2xl"
  if (density === "spacious") return "text-4xl"
  return "text-3xl"
}

function inlineSummaryClassName(density: LemmaDocumentWorkspaceDensity) {
  if (density === "compact") return "text-sm"
  if (density === "spacious") return "text-base"
  return "text-[15px]"
}

function inspectorSlideClassName(focused: boolean) {
  return focused ? "opacity-95" : "opacity-100"
}

function inspectorPaddingClassName(density: LemmaDocumentWorkspaceDensity) {
  if (density === "compact") return "p-3"
  if (density === "spacious") return "p-6"
  return "p-4"
}

function textFilePaddingClassName(density: LemmaDocumentWorkspaceDensity) {
  if (density === "compact") return "p-3"
  if (density === "spacious") return "p-6"
  return "p-4"
}

function commandPaddingClassName(density: LemmaDocumentWorkspaceDensity) {
  if (density === "compact") return "p-2"
  if (density === "spacious") return "p-4"
  return "p-3"
}

function titleClassName(density: LemmaDocumentWorkspaceDensity) {
  if (density === "compact") return "text-2xl"
  if (density === "spacious") return "text-4xl"
  return "text-[2rem]"
}

function summaryClassName(density: LemmaDocumentWorkspaceDensity) {
  if (density === "compact") return "text-sm"
  if (density === "spacious") return "text-base"
  return "text-[15px]"
}

function radiusClassName(radius: LemmaDocumentWorkspaceRadius, target: "surface" | "control" | "overlay") {
  if (radius === "none") return "rounded-none"
  if (radius === "sm") return target === "control" ? "rounded-sm" : "rounded-md"
  return target === "control" ? "rounded-md" : "rounded-lg"
}

function ScopedDocumentStyles() {
  return (
    <style>
      {`
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror {
  --lemma-doc-selection: rgba(35, 131, 226, 0.28);
  --lemma-doc-selection-strong: rgba(35, 131, 226, 0.18);
  --lemma-doc-border: hsl(var(--border));
  --lemma-doc-muted: hsl(var(--muted));
  --lemma-doc-muted-text: hsl(var(--muted-foreground));
  -webkit-user-select: text;
  -webkit-touch-callout: default;
  -webkit-font-variant-ligatures: none;
  user-select: text;
  caret-color: hsl(var(--foreground));
  font-feature-settings: "liga" 0;
  font-variant-ligatures: none;
  overflow-wrap: break-word;
  position: relative;
  color: hsl(var(--foreground));
  display: block;
  font-size: 16px;
  line-height: 1.72;
  max-width: none;
  text-align: left;
  white-space: pre-wrap;
  word-break: normal;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror:focus {
  outline: none;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror * {
  box-sizing: border-box;
  max-width: 100%;
  -webkit-user-select: text;
  user-select: text;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror p {
  display: block;
  font-weight: 400;
  margin: 0.5em 0;
  padding: 0.15rem 0;
  text-align: left;
  width: auto;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h1,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h2,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h3,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h4,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h5,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h6 {
  color: hsl(var(--foreground));
  font-weight: 650;
  letter-spacing: 0;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h1 {
  font-size: 1.875rem;
  line-height: 2.3rem;
  margin: 1.75rem 0 0.35rem;
  text-align: left;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h2 {
  font-size: 1.5rem;
  line-height: 2rem;
  margin: 1.5rem 0 0.3rem;
  text-align: left;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h3 {
  font-size: 1.25rem;
  line-height: 1.75rem;
  margin: 1.25rem 0 0.25rem;
  text-align: left;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h4 {
  font-size: 1.1rem;
  line-height: 1.6rem;
  margin: 1rem 0 0.2rem;
  text-align: left;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h5 {
  font-size: 1rem;
  line-height: 1.5rem;
  margin: 0.85rem 0 0.15rem;
  text-align: left;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror h6 {
  font-size: 0.9rem;
  line-height: 1.4rem;
  margin: 0.75rem 0 0.15rem;
  color: hsl(var(--muted-foreground));
  text-align: left;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror blockquote {
  border-left: 3px solid hsl(var(--primary) / 0.4);
  color: hsl(var(--foreground) / 0.82);
  margin: 0.75rem 0;
  padding: 0.25rem 0 0.25rem 1rem;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror pre {
  background: hsl(var(--muted) / 0.55);
  border: 1px solid hsl(var(--border) / 0.6);
  border-radius: 8px;
  color: hsl(var(--foreground));
  margin: 0.75rem 0;
  overflow-x: auto;
  padding: 1rem;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror code {
  background: hsl(var(--muted) / 0.5);
  border-radius: 4px;
  font-size: 0.88em;
  padding: 0.1rem 0.3rem;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror pre code {
  background: transparent;
  padding: 0;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror a {
  color: hsl(var(--primary));
  text-decoration: underline;
  text-underline-offset: 3px;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror hr {
  border: 0;
  border-top: 1px solid hsl(var(--border));
  margin: 1.5rem 0;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror img,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror video {
  border-radius: 8px;
  max-width: 100%;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .tableWrapper {
  margin: 0.75rem -4px;
  overflow-x: auto;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror table {
  border-collapse: collapse;
  table-layout: fixed;
  width: 100%;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror th,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror td {
  border: 1px solid hsl(var(--border));
  box-sizing: border-box;
  min-width: 6rem;
  padding: 0.4rem 0.75rem;
  position: relative;
  vertical-align: top;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror th {
  background: hsl(var(--muted) / 0.35);
  color: hsl(var(--foreground) / 0.8);
  font-size: 0.82rem;
  font-weight: 600;
  letter-spacing: 0.01em;
  text-align: left;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror td {
  background: transparent;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror th > p,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror td > p {
  margin: 0;
  padding: 0;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .selectedCell {
  --color: 210, 100%, 56%;
  background-color: rgba(35, 131, 226, 0.12) !important;
  border-color: rgba(35, 131, 226, 0.4) !important;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .column-resize-handle {
  background-color: rgba(35, 131, 226, 0.6) !important;
  width: 3px;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror.resize-cursor {
  cursor: col-resize;
}

.lemma-table-handle-trigger {
  align-items: center;
  background: hsl(var(--background));
  border: 1px solid hsl(var(--border) / 0.5);
  border-radius: 3px;
  box-shadow: 0 1px 2px hsl(var(--foreground) / 0.06);
  color: hsl(var(--muted-foreground) / 0.5);
  cursor: pointer;
  display: flex;
  justify-content: center;
  padding: 0;
  transition: background 0.15s, color 0.15s;
}

.lemma-table-handle-trigger:hover {
  background: hsl(var(--muted) / 0.5);
  color: hsl(var(--muted-foreground));
}

.lemma-table-handle-popover {
  background: hsl(var(--popover));
  border: 1px solid hsl(var(--border));
  border-radius: 8px;
  box-shadow: 0 4px 16px hsl(var(--foreground) / 0.1);
  color: hsl(var(--popover-foreground));
  max-height: 24rem;
  min-width: 8rem;
  overflow: auto;
  overscroll-behavior: none;
  padding: 4px;
  position: relative;
  user-select: none;
  white-space: nowrap;
  z-index: 50;
}

.lemma-table-handle-item {
  align-items: center;
  border-radius: 4px;
  cursor: default;
  display: flex;
  font-size: 13px;
  gap: 8px;
  justify-content: space-between;
  min-width: 8rem;
  padding: 5px 10px;
  user-select: none;
  transition: background 0.1s;
  white-space: nowrap;
}

.lemma-table-handle-item[data-highlighted] {
  background: hsl(var(--muted) / 0.5);
}

.lemma-table-handle-item[data-disabled] {
  opacity: 0.5;
  pointer-events: none;
}

.lemma-table-handle-item-danger {
  color: hsl(var(--destructive));
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .prosekit-placeholder::before {
  color: hsl(var(--muted-foreground));
  content: attr(data-placeholder);
  height: 0;
  opacity: 0.5;
  pointer-events: none;
  position: absolute;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .ProseMirror-selectednode {
  outline: 2px solid rgba(35, 131, 226, 0.5);
  outline-offset: 2px;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror::selection,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror *::selection {
  background: var(--lemma-doc-selection);
  color: inherit;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror::-moz-selection,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror *::-moz-selection {
  background: var(--lemma-doc-selection);
  color: inherit;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .selectedCell {
  background: var(--lemma-doc-selection-strong);
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .selection,
.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .ProseMirror-selectednode::after {
  background: var(--lemma-doc-selection);
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .prosemirror-flat-list {
  margin: 0.15rem 0;
  padding: 0;
  position: relative;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .prosemirror-flat-list > .list-marker {
  color: hsl(var(--muted-foreground));
  height: auto;
  inset-inline-start: 0;
  line-height: inherit;
  position: absolute;
  text-align: center;
  top: 0;
  width: 1lh;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .prosemirror-flat-list > .list-content {
  margin-inline-start: 1lh;
  min-width: 0;
  text-align: left;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .prosemirror-flat-list[data-list-kind="task"] > .list-marker {
  display: flex;
  align-items: center;
  height: 1lh;
  justify-content: center;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .prosemirror-flat-list[data-list-kind="task"] > .list-marker input {
  accent-color: hsl(var(--primary));
  margin: 0;
}

.lemma-document-workspace .lemma-prosekit-mount.ProseMirror .prosemirror-flat-list .prosemirror-flat-list {
  margin-left: 0;
  margin-top: 0.1rem;
}

.dark .lemma-document-workspace .lemma-prosekit-mount.ProseMirror {
  --lemma-doc-selection: rgba(35, 131, 226, 0.34);
  --lemma-doc-selection-strong: rgba(35, 131, 226, 0.2);
}
      `}
    </style>
  )
}
