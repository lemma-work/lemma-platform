import * as React from "react"
import { Bell, Check } from "lucide-react"
import { Badge } from "@/components/lemma/ui/badge"
import { Button } from "@/components/lemma/ui/button"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/lemma/ui/popover"
import { Separator } from "@/components/lemma/ui/separator"
import { Skeleton } from "@/components/lemma/ui/skeleton"
import { useRecords, useUpdateRecord } from "lemma-sdk/react"
import type { LemmaClient } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"

export type LemmaNotificationBellAppearance = "default" | "borderless" | "minimal" | "contained"
export type LemmaNotificationBellDensity = "compact" | "comfortable" | "spacious"
export type LemmaNotificationBellRadius = "none" | "sm" | "md" | "lg" | "xl" | "2xl" | "3xl" | "full"

export interface LemmaNotificationBellProps {
  client: LemmaClient
  podId?: string
  tableName?: string
  enabled?: boolean
  titleField?: string
  bodyField?: string
  readField?: string
  timestampField?: string
  limit?: number
  appearance?: LemmaNotificationBellAppearance
  density?: LemmaNotificationBellDensity
  radius?: LemmaNotificationBellRadius
  onNotificationClick?: (notification: Record<string, unknown>) => void
  className?: string
}

type NotificationRecord = Record<string, unknown>

const RADIUS_MAP: Record<LemmaNotificationBellRadius, Record<string, string>> = {
  none: { surface: "rounded-none", control: "rounded-none", pill: "rounded-none", overlay: "rounded-none" },
  sm: { surface: "rounded-sm", control: "rounded-sm", pill: "rounded-full", overlay: "rounded-sm" },
  md: { surface: "rounded-md", control: "rounded-md", pill: "rounded-full", overlay: "rounded-md" },
  lg: { surface: "rounded-lg", control: "rounded-md", pill: "rounded-full", overlay: "rounded-lg" },
  xl: { surface: "rounded-xl", control: "rounded-lg", pill: "rounded-full", overlay: "rounded-xl" },
  "2xl": { surface: "rounded-2xl", control: "rounded-xl", pill: "rounded-full", overlay: "rounded-2xl" },
  "3xl": { surface: "rounded-3xl", control: "rounded-2xl", pill: "rounded-full", overlay: "rounded-3xl" },
  full: { surface: "rounded-3xl", control: "rounded-full", pill: "rounded-full", overlay: "rounded-3xl" },
}

function bellRadiusClassName(
  radius: LemmaNotificationBellRadius,
  kind: "surface" | "control" | "pill" | "overlay",
): string {
  return RADIUS_MAP[radius]?.[kind] ?? RADIUS_MAP.lg[kind]
}

export function LemmaNotificationBell({
  client,
  podId,
  tableName = "notifications",
  enabled = true,
  titleField = "title",
  bodyField = "body",
  readField = "is_read",
  timestampField = "created_at",
  limit = 20,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  onNotificationClick,
  className,
}: LemmaNotificationBellProps) {
  const [open, setOpen] = React.useState(false)

  const { records, isLoading, refresh } = useRecords<NotificationRecord>({
    client,
    podId,
    tableName,
    limit,
    sortBy: timestampField,
    order: "desc",
    enabled,
  })

  const { update } = useUpdateRecord({
    client,
    podId,
    tableName,
    enabled,
    onSuccess: () => {
      refresh()
    },
  })

  const unreadCount = React.useMemo(
    () => records.filter((r) => !r[readField]).length,
    [records, readField],
  )

  const handleMarkRead = React.useCallback(
    async (notification: NotificationRecord) => {
      const id = String(notification.id ?? "")
      if (!id) return
      const isRead = notification[readField]
      if (!isRead) {
        await update({ [readField]: true }, { recordId: id })
      }
      onNotificationClick?.(notification)
    },
    [onNotificationClick, readField, update],
  )

  const handleMarkAllRead = React.useCallback(async () => {
    const unread = records.filter((r) => !r[readField])
    for (const notification of unread) {
      const id = String(notification.id ?? "")
      if (id) {
        await update({ [readField]: true }, { recordId: id })
      }
    }
  }, [records, readField, update])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        className={cn(
          "relative inline-flex items-center justify-center whitespace-nowrap font-medium transition-colors",
          triggerVariant(appearance) === "ghost" ? "border-transparent bg-transparent hover:bg-muted/40" : "border border-border bg-background hover:bg-muted/80",
          triggerClassName(appearance, radius),
          density === "compact" ? "size-7" : density === "spacious" ? "size-10" : "size-8",
          "rounded-md",
          className,
        )}
        disabled={!enabled}
      >
        <Bell className="size-4" />
        {unreadCount > 0 && (
          <Badge
            className={cn(
              "absolute -right-1 -top-1 flex size-5 items-center justify-center p-0 text-[10px] font-semibold",
                bellRadiusClassName(radius, "pill"),
              )}
            >
              {unreadCount > 99 ? "99+" : unreadCount}
            </Badge>
          )}
          <span className="sr-only">
            {unreadCount > 0 ? `${unreadCount} unread notifications` : "No unread notifications"}
          </span>
      </PopoverTrigger>

      <PopoverContent
        align="end"
        className={cn("w-80 p-0", popoverClassName(appearance, radius))}
      >
        <div className={cn("flex items-center justify-between", headerPadding(density), headerClassName(appearance))}>
          <div className="flex items-center gap-2">
            <span className={cn("flex size-6 items-center justify-center border border-border/50 bg-muted/40 text-muted-foreground", bellRadiusClassName(radius, "control"))}>
              <Bell className="size-3" />
            </span>
            <div className="min-w-0">
              <p className="text-sm font-semibold text-foreground">Notifications</p>
              {unreadCount > 0 && (
                <p className="text-xs text-muted-foreground">
                  {unreadCount} unread
                </p>
              )}
            </div>
          </div>
          {unreadCount > 0 && (
            <Button
              type="button"
              variant="ghost"
              size="sm"
              className="h-7 text-xs text-muted-foreground"
              onClick={() => void handleMarkAllRead()}
            >
              <Check className="mr-1 size-3" />
              Mark all read
            </Button>
          )}
        </div>

        <Separator />

        <div className={cn("max-h-80 overflow-auto", contentPadding(density))}>
          {isLoading ? (
            <div className="space-y-3">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="flex items-start gap-3">
                  <Skeleton className="size-2 mt-1.5 shrink-0 rounded-full" />
                  <div className="flex-1 space-y-1.5">
                    <Skeleton className="h-4 w-3/4" />
                    <Skeleton className="h-3 w-1/2" />
                  </div>
                </div>
              ))}
            </div>
          ) : records.length === 0 ? (
            <div className="flex min-h-32 flex-col items-center justify-center gap-2 text-center">
              <span className={cn("flex size-8 items-center justify-center border border-border/50 bg-muted/40 text-muted-foreground", bellRadiusClassName(radius, "pill"))}>
                <Bell className="size-3.5" />
              </span>
              <div>
                <p className="text-sm font-medium text-foreground">No notifications</p>
                <p className="mt-0.5 text-xs text-muted-foreground">You&apos;re all caught up.</p>
              </div>
            </div>
          ) : (
            <div className={cn("flex flex-col", density === "compact" ? "gap-1" : density === "spacious" ? "gap-3" : "gap-2")}>
              {records.map((notification) => {
                const id = String(notification.id ?? "")
                const title = String(notification[titleField] ?? "")
                const body = String(notification[bodyField] ?? "")
                const isRead = Boolean(notification[readField])
                const timestampVal = notification[timestampField]
                const timestamp = timestampVal ? new Date(String(timestampVal)) : null
                const isValidTimestamp = timestamp && !Number.isNaN(timestamp.getTime())

                return (
                  <button
                    key={id}
                    type="button"
                    onClick={() => void handleMarkRead(notification)}
                    className={cn(
                      "flex items-start gap-2.5 text-left transition-colors",
                      isRead ? "cursor-default" : "cursor-pointer hover:bg-muted/30",
                      notificationItemClassName(density, radius),
                    )}
                  >
                    <span className="mt-1.5 shrink-0">
                      {isRead ? (
                        <span className="block size-2 rounded-full bg-muted-foreground/20" />
                      ) : (
                        <span className="block size-2 rounded-full bg-primary" />
                      )}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className={cn("block truncate text-sm", isRead ? "text-muted-foreground" : "font-medium text-foreground")}>
                        {title || "Untitled"}
                      </span>
                      {body && (
                        <span className="mt-0.5 block truncate text-xs text-muted-foreground">
                          {body}
                        </span>
                      )}
                      {isValidTimestamp && (
                        <span className="mt-0.5 block text-[10px] text-muted-foreground/70">
                          {formatRelativeTime(timestamp!)}
                        </span>
                      )}
                    </span>
                  </button>
                )
              })}
            </div>
          )}
        </div>

        {records.length > 0 && (
          <>
            <Separator />
            <div className={cn("flex items-center justify-center", footerPadding(density))}>
              <p className="text-[11px] text-muted-foreground">
                {records.length} notification{records.length !== 1 ? "s" : ""}
              </p>
            </div>
          </>
        )}
      </PopoverContent>
    </Popover>
  )
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

function triggerVariant(appearance: LemmaNotificationBellAppearance) {
  return appearance === "minimal" || appearance === "borderless" ? "ghost" : "outline"
}

function triggerClassName(appearance: LemmaNotificationBellAppearance, radius: LemmaNotificationBellRadius) {
  return cn(
    bellRadiusClassName(radius, "control"),
    appearance === "minimal" ? "border-transparent bg-transparent shadow-none hover:bg-muted/40" : null,
    appearance === "borderless" ? "border-transparent bg-transparent shadow-none" : null,
  )
}

function popoverClassName(appearance: LemmaNotificationBellAppearance, radius: LemmaNotificationBellRadius) {
  return cn(
    bellRadiusClassName(radius, "overlay"),
    appearance === "minimal" ? "border-transparent bg-background/95 shadow-none ring-1 ring-border/15" : null,
    appearance === "borderless" ? "border-transparent shadow-2xl ring-0" : null,
    appearance === "contained" ? "border-border/80 bg-card shadow-xl" : null,
  )
}

function headerClassName(appearance: LemmaNotificationBellAppearance) {
  if (appearance === "borderless") return "border-b border-transparent"
  if (appearance === "minimal") return "border-b border-border/15"
  if (appearance === "contained") return "border-b border-border/60 bg-card"
  return "border-b border-border/40 bg-card/95"
}

function headerPadding(density: LemmaNotificationBellDensity) {
  if (density === "compact") return "gap-2 px-3 py-2"
  if (density === "spacious") return "gap-4 px-5 py-4"
  return "gap-3 px-4 py-3"
}

function contentPadding(density: LemmaNotificationBellDensity) {
  if (density === "compact") return "p-2"
  if (density === "spacious") return "p-5"
  return "p-4"
}

function footerPadding(density: LemmaNotificationBellDensity) {
  if (density === "compact") return "px-3 py-1.5"
  if (density === "spacious") return "px-5 py-3"
  return "px-4 py-2"
}

function notificationItemClassName(density: LemmaNotificationBellDensity, radius: LemmaNotificationBellRadius) {
  if (density === "compact") return cn("px-1.5 py-1.5", bellRadiusClassName(radius, "control"))
  if (density === "spacious") return cn("px-3 py-3", bellRadiusClassName(radius, "control"))
  return cn("px-2 py-2", bellRadiusClassName(radius, "control"))
}
