import * as React from "react"
import {
  MessageSquare,
  RefreshCw,
  Send,
  User,
} from "lucide-react"
import { Button } from "@/components/lemma/ui/button"
import { Skeleton } from "@/components/lemma/ui/skeleton"
import { Textarea } from "@/components/lemma/ui/textarea"
import { useReferencingRecords, useCreateRecord } from "lemma-sdk/react"
import type { LemmaClient } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"
import { enumPillClasses, type EnumColorMap } from "./comments-enum-utils"
import {
  commentsRadiusClassName,
  type LemmaCommentsAppearance,
  type LemmaCommentsDensity,
  type LemmaCommentsRadius,
} from "./comments-style-utils"

export type { LemmaCommentsAppearance, LemmaCommentsDensity, LemmaCommentsRadius } from "./comments-style-utils"

export interface LemmaCommentsProps {
  client: LemmaClient
  podId?: string
  tableName: string
  foreignKey: string
  recordId: string
  enabled?: boolean

  bodyField?: string
  authorField?: string
  createdAtField?: string

  submitVia?: "direct" | "function"
  submitFunctionName?: string

  enumColorMap?: EnumColorMap

  appearance?: LemmaCommentsAppearance
  density?: LemmaCommentsDensity
  radius?: LemmaCommentsRadius

  onCommentClick?: (comment: Record<string, unknown>) => void
  title?: React.ReactNode
  headerActions?: React.ReactNode
  className?: string
}

export function LemmaComments({
  client,
  podId,
  tableName,
  foreignKey,
  recordId,
  enabled = true,
  bodyField = "body",
  authorField = "author_user_id",
  createdAtField = "created_at",
  submitVia = "direct",
  submitFunctionName,
  enumColorMap,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  onCommentClick,
  title,
  headerActions,
  className,
}: LemmaCommentsProps) {
  const [newBody, setNewBody] = React.useState("")

  const commentsState = useReferencingRecords({
    client,
    podId,
    table: tableName,
    foreignKey,
    recordId,
    sortBy: createdAtField,
    order: "asc",
    enabled,
  })

  const createRecord = useCreateRecord({
    client,
    podId,
    tableName,
    enabled,
    createVia: submitVia,
    createFunctionName: submitFunctionName,
    onSuccess: () => {
      setNewBody("")
      commentsState.refresh()
    },
  })

  const handleSubmit = async () => {
    const trimmed = newBody.trim()
    if (!trimmed || createRecord.isSubmitting) return
    await createRecord.create({
      [bodyField]: trimmed,
      [foreignKey]: recordId,
    })
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
      e.preventDefault()
      handleSubmit()
    }
  }

  const isLoading = commentsState.isLoading
  const hasError = commentsState.error
  const comments = commentsState.records

  return (
    <div
      data-appearance={appearance}
      data-density={density}
      data-radius={radius}
      className={cn("lemma-comments flex h-full min-h-0 flex-col", commentsRootClassName(appearance), className)}
    >
      <div className={cn("shrink-0", commentsHeaderClassName(appearance))}>
        <div className={cn("flex items-center justify-between", commentsToolbarClassName(density))}>
          <div className="min-w-0 flex items-center gap-3">
            <span className={cn("flex size-7 items-center justify-center border border-border/50 bg-muted/40 text-muted-foreground", commentsRadiusClassName(radius, "control"))}>
              <MessageSquare className="size-3.5" />
            </span>
            <div className="min-w-0">
              <h1 className="truncate text-sm font-semibold text-foreground">
                {title ?? "Comments"}
              </h1>
              <p className="text-xs text-muted-foreground">
                {comments.length} comment{comments.length !== 1 ? "s" : ""}
              </p>
            </div>
          </div>
          <div className="flex items-center gap-2">
            {headerActions}
          </div>
        </div>
      </div>

      <div className={cn("flex-1 overflow-auto", commentsContentClassName(density))}>
        {hasError ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
            <p className="text-sm text-destructive">{hasError.message}</p>
            <Button variant="outline" size="sm" onClick={() => commentsState.refresh()}>
              <RefreshCw className="mr-2 size-3.5" />
              Retry
            </Button>
          </div>
        ) : isLoading ? (
          <div className="space-y-4">
            {Array.from({ length: 4 }).map((_, i) => (
              <div key={i} className="flex items-start gap-3">
                <Skeleton className="size-8 rounded-full shrink-0" />
                <div className="flex-1 space-y-2">
                  <Skeleton className="h-4 w-1/4" />
                  <Skeleton className="h-3 w-3/4" />
                </div>
              </div>
            ))}
          </div>
        ) : comments.length === 0 ? (
          <div className="flex min-h-48 flex-col items-center justify-center gap-3 text-center">
            <div className={cn("flex size-10 items-center justify-center border border-border/60 bg-muted/40 text-muted-foreground", commentsRadiusClassName(radius, "pill"))}>
              <MessageSquare className="size-5" />
            </div>
            <div>
              <p className="font-medium text-foreground">No comments yet</p>
              <p className="mt-1 text-sm text-muted-foreground">Start the conversation by adding a comment below.</p>
            </div>
          </div>
        ) : (
          <div className={cn("flex flex-col", density === "compact" ? "gap-2" : density === "spacious" ? "gap-5" : "gap-3")}>
            {comments.map((comment, index) => {
              const author = String(comment[authorField] ?? "")
              const body = String(comment[bodyField] ?? "")
              const createdAt = comment[createdAtField] ? new Date(String(comment[createdAtField])) : null

              return (
                <button
                  key={String(comment.id ?? index)}
                  type="button"
                  onClick={() => onCommentClick?.(comment)}
                  className={cn(
                    "grid grid-cols-[auto_minmax(0,1fr)] gap-3 text-left transition-colors",
                    onCommentClick && "cursor-pointer hover:bg-muted/30",
                    !onCommentClick && "cursor-default",
                    commentsRadiusClassName(radius, "control"),
                  )}
                >
                  <div className={cn("flex size-8 shrink-0 items-center justify-center border border-border/50 bg-muted/40 text-xs font-medium text-muted-foreground", commentsRadiusClassName(radius, "pill"))}>
                    {author ? getInitials(author) : <User className="size-3.5" />}
                  </div>
                  <div className="min-w-0">
                    <div className="flex items-center gap-2">
                      <span className="truncate text-sm font-medium text-foreground">
                        {author || "Unknown"}
                      </span>
                      {createdAt && !Number.isNaN(createdAt.getTime()) && (
                        <span className="shrink-0 text-[10px] text-muted-foreground">
                          {formatRelativeTime(createdAt)}
                        </span>
                      )}
                    </div>
                    <p className={cn("mt-0.5 text-sm text-foreground/80 whitespace-pre-wrap", density === "compact" ? "line-clamp-3" : "")}>
                      {body}
                    </p>
                  </div>
                </button>
              )
            })}
          </div>
        )}
      </div>

      <div className={cn("shrink-0 border-t border-border/30", commentsInputClassName(appearance, density))}>
        <Textarea
          value={newBody}
          onChange={(e) => setNewBody(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder="Add a comment..."
          disabled={createRecord.isSubmitting}
          className={cn("min-h-[60px] resize-none text-sm", commentsRadiusClassName(radius, "surface"))}
        />
        <div className="flex items-center justify-between pt-2">
          <span className="text-[10px] text-muted-foreground">
            {newBody.trim().length > 0 ? "Ctrl+Enter to send" : "\u00A0"}
          </span>
          <Button
            size="sm"
            onClick={handleSubmit}
            disabled={!newBody.trim() || createRecord.isSubmitting}
            className={cn("h-7 gap-1.5 text-xs", commentsRadiusClassName(radius, "control"))}
          >
            <Send className="size-3" />
            Send
          </Button>
        </div>
      </div>
    </div>
  )
}

function getInitials(name: string): string {
  const parts = name.trim().split(/[\s_]+/).filter(Boolean)
  if (parts.length === 0) return "?"
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase()
}

function formatRelativeTime(date: Date): string {
  const now = new Date()
  const diff = now.getTime() - date.getTime()
  const seconds = Math.floor(diff / 1000)
  const minutes = Math.floor(seconds / 60)
  const hours = Math.floor(minutes / 60)
  const days = Math.floor(hours / 24)

  if (seconds < 60) return "just now"
  if (minutes < 60) return `${minutes}m ago`
  if (hours < 24) return `${hours}h ago`
  if (days < 7) return `${days}d ago`
  return date.toLocaleDateString(undefined, { month: "short", day: "numeric" })
}

function commentsRootClassName(appearance: LemmaCommentsAppearance) {
  if (appearance === "contained") return "bg-card"
  if (appearance === "minimal" || appearance === "borderless") return "bg-transparent"
  return "bg-background"
}

function commentsHeaderClassName(appearance: LemmaCommentsAppearance) {
  if (appearance === "borderless") return "bg-transparent"
  if (appearance === "minimal") return "border-b border-border/15 bg-transparent"
  if (appearance === "contained") return "border-b border-border/60 bg-card"
  return "border-b border-border/40 bg-card/95"
}

function commentsToolbarClassName(density: LemmaCommentsDensity) {
  if (density === "compact") return "gap-2 px-3 py-2"
  if (density === "spacious") return "gap-4 px-5 py-4"
  return "gap-3 px-4 py-3"
}

function commentsContentClassName(density: LemmaCommentsDensity) {
  if (density === "compact") return "p-2"
  if (density === "spacious") return "p-5"
  return "p-4"
}

function commentsInputClassName(appearance: LemmaCommentsAppearance, density: LemmaCommentsDensity) {
  const pad = density === "compact" ? "p-2" : density === "spacious" ? "p-4" : "p-3"
  if (appearance === "borderless") return `bg-transparent ${pad}`
  if (appearance === "minimal") return `bg-transparent ${pad}`
  if (appearance === "contained") return `bg-card ${pad}`
  return `bg-card/95 ${pad}`
}
