import * as React from "react"
import {
  ChevronDown,
  ChevronRight,
  FileText,
  Folder,
  FolderOpen,
  Plus,
  RefreshCw,
} from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import { Skeleton } from "@/components/lemma/ui/skeleton"
import { useFileTree, useRecords } from "lemma-sdk/react"
import type {
  DatastoreDirectoryTreeNode,
  DatastoreFileNamespace,
  LemmaClient,
} from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import { type EnumColorMap } from "./page-tree-enum-utils"
import {
  pageTreeRadiusClassName,
  type LemmaPageTreeAppearance,
  type LemmaPageTreeDensity,
  type LemmaPageTreeRadius,
} from "./page-tree-style-utils"

export type {
  LemmaPageTreeAppearance,
  LemmaPageTreeDensity,
  LemmaPageTreeRadius,
} from "./page-tree-style-utils"
export type { EnumColorMap, EnumColorEntry } from "./page-tree-enum-utils"

export type LemmaPageTreeDataSource = "files" | "records"

export interface TreeNode {
  id: string
  label: string
  kind: "directory" | "file" | "record"
  path?: string
  record?: Record<string, unknown>
  sourceNode?: DatastoreDirectoryTreeNode
  iconText?: string | null
  children: TreeNode[]
}

export interface LemmaPageTreeProps {
  client: LemmaClient
  podId?: string
  enabled?: boolean
  dataSource?: LemmaPageTreeDataSource

  rootPath?: string
  filesPerDirectory?: number
  namespace?: DatastoreFileNamespace
  selectedPath?: string
  onSelect?: (node: TreeNode) => void
  onCreateDocument?: (directoryPath?: string) => void
  defaultExpandedPaths?: string[]

  tableName?: string
  parentField?: string
  titleField?: string
  iconField?: string
  selectedId?: string
  onPageClick?: (payload: Record<string, unknown> | TreeNode) => void
  onCreatePage?: (parentId?: string) => void
  defaultExpandedIds?: string[]
  onReorder?: (recordId: string, newParentId: string | null, newIndex: number) => void
  enumColorMap?: EnumColorMap

  appearance?: LemmaPageTreeAppearance
  density?: LemmaPageTreeDensity
  radius?: LemmaPageTreeRadius
  title?: React.ReactNode
  className?: string
}

export function LemmaPageTree({
  client,
  podId,
  enabled = true,
  dataSource,
  rootPath = "/",
  filesPerDirectory = 3,
  namespace = "POD",
  selectedPath,
  onSelect,
  onCreateDocument,
  defaultExpandedPaths,
  tableName,
  parentField = "parent_page_id",
  titleField = "title",
  iconField,
  selectedId,
  onPageClick,
  onCreatePage,
  defaultExpandedIds,
  onReorder,
  enumColorMap,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  title,
  className,
}: LemmaPageTreeProps) {
  const resolvedDataSource =
    dataSource ?? (tableName ? "records" : "files")
  const normalizedRootPath = normalizePath(rootPath)
  const initialExpanded = React.useMemo(
    () => new Set(defaultExpandedPaths ?? defaultExpandedIds ?? []),
    [defaultExpandedIds, defaultExpandedPaths],
  )
  const [expandedIds, setExpandedIds] = React.useState<Set<string>>(
    () => initialExpanded,
  )

  const fileTreeState = useFileTree({
    client,
    podId,
    rootPath: normalizedRootPath,
    filesPerDirectory,
    namespace,
    enabled: enabled && resolvedDataSource === "files",
  })

  const recordsState = useRecords({
    client,
    podId,
    tableName: tableName ?? "pages",
    enabled: enabled && resolvedDataSource === "records" && Boolean(tableName),
    limit: 500,
  })

  React.useEffect(() => {
    if (
      resolvedDataSource !== "records" ||
      !tableName ||
      recordsState.isLoading ||
      recordsState.isLoadingMore ||
      !recordsState.nextPageToken ||
      recordsState.records.length >= recordsState.total
    ) {
      return
    }

    void recordsState.loadMore()
  }, [
    recordsState.isLoading,
    recordsState.isLoadingMore,
    recordsState.loadMore,
    recordsState.nextPageToken,
    recordsState.records.length,
    recordsState.total,
    resolvedDataSource,
    tableName,
  ])

  React.useEffect(() => {
    if (resolvedDataSource !== "files" || !selectedPath) return

    setExpandedIds((previous) => {
      const next = new Set(previous)
      for (const ancestorPath of ancestorPaths(selectedPath)) {
        next.add(ancestorPath)
      }
      return next
    })
  }, [resolvedDataSource, selectedPath])

  const tree = React.useMemo(() => {
    if (resolvedDataSource === "files") {
      return buildFileTreeRoots(fileTreeState.tree, normalizedRootPath)
    }

    return buildRecordTree(recordsState.records, parentField, titleField, iconField)
  }, [
    fileTreeState.tree,
    iconField,
    normalizedRootPath,
    parentField,
    recordsState.records,
    resolvedDataSource,
    titleField,
  ])

  const totalItems = React.useMemo(() => countTreeNodes(tree), [tree])

  const toggleExpand = React.useCallback((id: string) => {
    setExpandedIds((previous) => {
      const next = new Set(previous)
      if (next.has(id)) {
        next.delete(id)
      } else {
        next.add(id)
      }
      return next
    })
  }, [])

  const handleRefresh = React.useCallback(() => {
    if (resolvedDataSource === "files") {
      void fileTreeState.refresh()
      return
    }

    void recordsState.refresh()
  }, [fileTreeState, recordsState, resolvedDataSource])

  const handleSelect = React.useCallback(
    (node: TreeNode) => {
      onSelect?.(node)
      if (resolvedDataSource === "records") {
        onPageClick?.(node.record ?? {})
        return
      }
      onPageClick?.(node)
    },
    [onPageClick, onSelect, resolvedDataSource],
  )

  const handleCreate = React.useCallback(
    (target?: string) => {
      if (resolvedDataSource === "files") {
        onCreateDocument?.(target)
        if (!onCreateDocument) onCreatePage?.(target)
        return
      }

      onCreatePage?.(target)
    },
    [onCreateDocument, onCreatePage, resolvedDataSource],
  )

  const canCreate =
    resolvedDataSource === "files"
      ? Boolean(onCreateDocument ?? onCreatePage)
      : Boolean(onCreatePage)
  const isLoading =
    resolvedDataSource === "files"
      ? fileTreeState.isLoading
      : recordsState.isLoading || recordsState.isLoadingMore
  const error =
    resolvedDataSource === "files" ? fileTreeState.error : recordsState.error

  return (
    <div
      data-appearance={appearance}
      data-density={density}
      data-radius={radius}
      className={cn(
        "lemma-page-tree flex h-full min-h-0 flex-col",
        rootClassName(appearance),
        className,
      )}
    >
      <div className={cn("shrink-0", headerClassName(appearance))}>
        <div
          className={cn(
            "flex items-center justify-between gap-3",
            headerPaddingClassName(density),
          )}
        >
          <div className="min-w-0 flex items-center gap-3">
            <span
              className={cn(
                "flex size-8 items-center justify-center border border-border/50 bg-muted/40 text-muted-foreground",
                pageTreeRadiusClassName(radius, "control"),
              )}
            >
              {resolvedDataSource === "files" ? (
                <Folder className="size-4" />
              ) : (
                <FileText className="size-4" />
              )}
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold text-foreground">
                {title ?? (resolvedDataSource === "files" ? "Documents" : "Pages")}
              </h1>
              <p className="truncate text-xs text-muted-foreground">
                {resolvedDataSource === "files"
                  ? `${totalItems} item${totalItems !== 1 ? "s" : ""}`
                  : `${totalItems} page${totalItems !== 1 ? "s" : ""}`}
              </p>
            </div>
          </div>
          <div className="flex shrink-0 items-center gap-2">
            <Button
              variant="ghost"
              size="icon-sm"
              onClick={handleRefresh}
              disabled={isLoading}
            >
              <RefreshCw className={cn(isLoading ? "animate-spin" : undefined)} />
            </Button>
            {canCreate ? (
              <Button
                size="sm"
                onClick={() =>
                  handleCreate(
                    resolvedDataSource === "files" ? normalizedRootPath : undefined,
                  )
                }
              >
                <Plus data-icon="inline-start" />
                New
              </Button>
            ) : null}
          </div>
        </div>
      </div>

      <div className={cn("flex-1 overflow-auto", contentPaddingClassName(density))}>
        {error ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
            <p className="text-sm text-destructive">{error.message}</p>
            <Button variant="outline" size="sm" onClick={handleRefresh}>
              <RefreshCw data-icon="inline-start" />
              Retry
            </Button>
          </div>
        ) : isLoading ? (
          <div className="flex flex-col gap-1">
            {[
              { depth: 0, width: "w-32" },
              { depth: 1, width: "w-24" },
              { depth: 1, width: "w-20" },
              { depth: 0, width: "w-28" },
              { depth: 0, width: "w-36" },
            ].map((item, index) => (
              <div
                key={index}
                className="flex items-center gap-2 py-1"
                style={{ paddingLeft: `${item.depth * indentPx(density)}px` }}
              >
                <Skeleton className="size-3.5 shrink-0 rounded-sm" />
                <Skeleton className={cn("h-4", item.width)} />
              </div>
            ))}
          </div>
        ) : tree.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
            <span
              className={cn(
                "flex size-11 items-center justify-center border border-border/60 bg-muted/40 text-muted-foreground",
                pageTreeRadiusClassName(radius, "pill"),
              )}
            >
              {resolvedDataSource === "files" ? (
                <Folder className="size-5" />
              ) : (
                <FileText className="size-5" />
              )}
            </span>
            <div>
              <p className="font-medium text-foreground">
                {resolvedDataSource === "files" ? "No documents yet" : "No pages yet"}
              </p>
              <p className="mt-1 text-sm text-muted-foreground">
                {resolvedDataSource === "files"
                  ? "Add your first document to shape the workspace tree."
                  : "Create your first page to get started."}
              </p>
            </div>
            {canCreate ? (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  handleCreate(
                    resolvedDataSource === "files" ? normalizedRootPath : undefined,
                  )
                }
              >
                <Plus data-icon="inline-start" />
                {resolvedDataSource === "files" ? "New document" : "New page"}
              </Button>
            ) : null}
          </div>
        ) : (
          <div className="flex flex-col">
            {tree.map((node) => (
              <PageTreeNode
                key={node.id}
                node={node}
                dataSource={resolvedDataSource}
                selectedId={selectedId}
                selectedPath={selectedPath}
                expandedIds={expandedIds}
                onToggleExpand={toggleExpand}
                onSelect={handleSelect}
                onCreate={canCreate ? handleCreate : undefined}
                density={density}
                radius={radius}
              />
            ))}
          </div>
        )}
      </div>
    </div>
  )
}

function PageTreeNode({
  node,
  dataSource,
  selectedId,
  selectedPath,
  expandedIds,
  onToggleExpand,
  onSelect,
  onCreate,
  density,
  radius,
}: {
  node: TreeNode
  dataSource: LemmaPageTreeDataSource
  selectedId?: string
  selectedPath?: string
  expandedIds: Set<string>
  onToggleExpand: (id: string) => void
  onSelect: (node: TreeNode) => void
  onCreate?: (target?: string) => void
  density: LemmaPageTreeDensity
  radius: LemmaPageTreeRadius
}) {
  const hasChildren = node.children.length > 0
  const isExpanded = expandedIds.has(node.id)
  const isSelected =
    dataSource === "files"
      ? Boolean(node.path && node.path === selectedPath)
      : node.id === selectedId
  const createTarget =
    dataSource === "files"
      ? node.kind === "directory"
        ? node.path
        : node.path
          ? directoryPathFromFile(node.path)
          : undefined
      : node.id

  return (
    <div>
      <div
        className={cn(
          "group flex cursor-pointer items-center transition-colors",
          rowClassName(density),
          isSelected && "border-l-2 border-primary bg-primary/10",
          !isSelected && "hover:bg-muted/50",
        )}
        onClick={() => onSelect(node)}
      >
        {hasChildren ? (
          <button
            type="button"
            className={cn(
              "flex shrink-0 items-center justify-center p-0.5 transition-colors hover:bg-muted/80",
              pageTreeRadiusClassName(radius, "control"),
            )}
            onClick={(event) => {
              event.stopPropagation()
              onToggleExpand(node.id)
            }}
          >
            {isExpanded ? (
              <ChevronDown className={chevronSize(density)} />
            ) : (
              <ChevronRight className={chevronSize(density)} />
            )}
          </button>
        ) : (
          <span className={spacerClassName(density)} />
        )}

        {node.kind === "directory" ? (
          isExpanded ? (
            <FolderOpen className={cn("shrink-0 text-muted-foreground", iconSize(density))} />
          ) : (
            <Folder className={cn("shrink-0 text-muted-foreground", iconSize(density))} />
          )
        ) : node.iconText ? (
          <span className={cn("shrink-0 leading-none", iconSize(density))}>
            {node.iconText}
          </span>
        ) : (
          <FileText className={cn("shrink-0 text-muted-foreground", iconSize(density))} />
        )}

        <span
          className={cn(
            "flex-1 truncate",
            titleSize(density),
            isSelected ? "font-medium text-foreground" : "text-foreground/90",
          )}
        >
          {node.label}
        </span>

        {onCreate ? (
          <button
            type="button"
            className={cn(
              "shrink-0 p-0.5 opacity-0 transition-opacity group-hover:opacity-100 hover:bg-muted/80",
              pageTreeRadiusClassName(radius, "control"),
            )}
            onClick={(event) => {
              event.stopPropagation()
              onCreate(createTarget)
            }}
          >
            <Plus className={plusSize(density)} />
          </button>
        ) : null}
      </div>

      {hasChildren && isExpanded ? (
        <div className={indentClassName(density)}>
          {node.children.map((child) => (
            <PageTreeNode
              key={child.id}
              node={child}
              dataSource={dataSource}
              selectedId={selectedId}
              selectedPath={selectedPath}
              expandedIds={expandedIds}
              onToggleExpand={onToggleExpand}
              onSelect={onSelect}
              onCreate={onCreate}
              density={density}
              radius={radius}
            />
          ))}
        </div>
      ) : null}
    </div>
  )
}

function buildFileTreeRoots(
  root: DatastoreDirectoryTreeNode | null,
  rootPath: string,
): TreeNode[] {
  if (!root) return []

  const mappedRoot = mapDirectoryNode(root)
  if (mappedRoot.path === rootPath && mappedRoot.kind === "directory") {
    return mappedRoot.children
  }

  return [mappedRoot]
}

function mapDirectoryNode(node: DatastoreDirectoryTreeNode): TreeNode {
  const path = normalizePath(node.path)
  const isDirectory = isDirectoryNode(node)
  return {
    id: path,
    label: displayTreeLabel(node.name, path),
    kind: isDirectory ? "directory" : "file",
    path,
    sourceNode: node,
    children: Array.isArray(node.children)
      ? node.children.map(mapDirectoryNode)
      : [],
  }
}

function buildRecordTree(
  records: Record<string, unknown>[],
  parentField: string,
  titleField: string,
  iconField?: string,
): TreeNode[] {
  const nodeMap = new Map<string, TreeNode>()
  const roots: TreeNode[] = []

  for (const record of records) {
    const id = record.id
    if (id == null) continue

    const idString = String(id)
    nodeMap.set(idString, {
      id: idString,
      label: String(record[titleField] ?? "Untitled"),
      kind: "record",
      record,
      iconText: iconField ? String(record[iconField] ?? "") || null : null,
      children: [],
    })
  }

  for (const record of records) {
    const id = record.id
    if (id == null) continue

    const idString = String(id)
    const node = nodeMap.get(idString)
    if (!node) continue

    const parentId = record[parentField]
    if (parentId == null || parentId === "") {
      roots.push(node)
      continue
    }

    const parentNode = nodeMap.get(String(parentId))
    if (parentNode && parentNode.id !== node.id) {
      parentNode.children.push(node)
    } else {
      roots.push(node)
    }
  }

  return sortTreeNodes(roots)
}

function sortTreeNodes(nodes: TreeNode[]): TreeNode[] {
  nodes.sort((left, right) => left.label.localeCompare(right.label))
  for (const node of nodes) {
    sortTreeNodes(node.children)
  }
  return nodes
}

function countTreeNodes(nodes: TreeNode[]): number {
  return nodes.reduce((count, node) => count + 1 + countTreeNodes(node.children), 0)
}

function ancestorPaths(path: string): string[] {
  const normalized = normalizePath(path)
  const parts = normalized.split("/").filter(Boolean)
  const ancestors: string[] = []

  for (let index = 0; index < parts.length - 1; index += 1) {
    ancestors.push(`/${parts.slice(0, index + 1).join("/")}`)
  }

  return ancestors
}

function directoryPathFromFile(path: string): string {
  const normalized = normalizePath(path)
  const parts = normalized.split("/").filter(Boolean)
  if (parts.length <= 1) return "/"
  return `/${parts.slice(0, -1).join("/")}`
}

function normalizePath(path: string): string {
  const trimmed = path.trim().replace(/\\/g, "/")
  if (!trimmed || trimmed === "/") return "/"
  return `/${trimmed.replace(/^\/+/, "").replace(/\/+/g, "/")}`
}

function displayTreeLabel(name: string, path: string): string {
  const trimmedName = name.trim()
  if (trimmedName) return trimmedName
  if (path === "/") return "Root"
  const parts = path.split("/").filter(Boolean)
  return parts[parts.length - 1] ?? path
}

function isDirectoryNode(node: DatastoreDirectoryTreeNode): boolean {
  const kind = String(node.kind ?? "").toLowerCase()
  return kind.includes("dir") || kind.includes("folder")
}

function rootClassName(appearance: LemmaPageTreeAppearance) {
  if (appearance === "contained") return "bg-card"
  if (appearance === "minimal" || appearance === "borderless") return "bg-transparent"
  return "bg-background"
}

function headerClassName(appearance: LemmaPageTreeAppearance) {
  if (appearance === "borderless") return "bg-transparent"
  if (appearance === "minimal") return "border-b border-border/15 bg-transparent"
  if (appearance === "contained") return "border-b border-border/60 bg-card"
  return "border-b border-border/40 bg-card/95"
}

function headerPaddingClassName(density: LemmaPageTreeDensity) {
  if (density === "compact") return "px-3 py-2"
  if (density === "spacious") return "px-5 py-4"
  return "px-4 py-3"
}

function contentPaddingClassName(density: LemmaPageTreeDensity) {
  if (density === "compact") return "p-1"
  if (density === "spacious") return "p-3"
  return "p-2"
}

function rowClassName(density: LemmaPageTreeDensity) {
  if (density === "compact") return "gap-1 px-2 py-1"
  if (density === "spacious") return "gap-2 px-4 py-2.5"
  return "gap-1.5 px-3 py-1.5"
}

function indentClassName(density: LemmaPageTreeDensity) {
  if (density === "compact") return "pl-4"
  if (density === "spacious") return "pl-8"
  return "pl-6"
}

function indentPx(density: LemmaPageTreeDensity) {
  if (density === "compact") return 16
  if (density === "spacious") return 32
  return 24
}

function chevronSize(density: LemmaPageTreeDensity) {
  if (density === "compact") return "size-3"
  if (density === "spacious") return "size-4"
  return "size-3.5"
}

function spacerClassName(density: LemmaPageTreeDensity) {
  if (density === "compact") return "w-4 shrink-0"
  if (density === "spacious") return "w-6 shrink-0"
  return "w-5 shrink-0"
}

function iconSize(density: LemmaPageTreeDensity) {
  if (density === "compact") return "size-3.5"
  if (density === "spacious") return "size-4.5"
  return "size-4"
}

function titleSize(density: LemmaPageTreeDensity) {
  if (density === "compact") return "text-xs"
  return "text-sm"
}

function plusSize(density: LemmaPageTreeDensity) {
  if (density === "compact") return "size-2.5"
  if (density === "spacious") return "size-3.5"
  return "size-3"
}
