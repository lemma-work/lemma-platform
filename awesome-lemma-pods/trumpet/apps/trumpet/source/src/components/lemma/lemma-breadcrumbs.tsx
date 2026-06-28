import * as React from "react"
import {
  Breadcrumb,
  BreadcrumbEllipsis,
  BreadcrumbItem,
  BreadcrumbLink,
  BreadcrumbList,
  BreadcrumbPage,
  BreadcrumbSeparator,
} from "@/components/lemma/ui/breadcrumb"
import { cn } from "@/components/lemma/lib/utils"

export type LemmaBreadcrumbAppearance = "default" | "minimal" | "borderless" | "contained"
export type LemmaBreadcrumbDensity = "compact" | "comfortable" | "spacious"
export type LemmaBreadcrumbRadius = "none" | "sm" | "md" | "lg" | "xl"

export interface LemmaBreadcrumbItem {
  label: React.ReactNode
  href?: string
  title?: string
  icon?: React.ReactNode
  current?: boolean
  onClick?: () => void
}

export interface LemmaBreadcrumbsProps {
  items: LemmaBreadcrumbItem[]
  maxItems?: number
  appearance?: LemmaBreadcrumbAppearance
  density?: LemmaBreadcrumbDensity
  radius?: LemmaBreadcrumbRadius
  className?: string
  onNavigate?: (item: LemmaBreadcrumbItem) => void
}

export function LemmaBreadcrumbs({
  items,
  maxItems = 5,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  className,
  onNavigate,
}: LemmaBreadcrumbsProps) {
  const visibleItems = React.useMemo(() => collapseBreadcrumbItems(items, maxItems), [items, maxItems])

  if (items.length === 0) return null

  return (
    <Breadcrumb
      data-appearance={appearance}
      data-density={density}
      data-radius={radius}
      className={cn("min-w-0", className)}
    >
      <BreadcrumbList className={cn("flex-nowrap", densityClassName(density))}>
        {visibleItems.map((item, index) => {
          const isEllipsis = item === null
          const isLast = index === visibleItems.length - 1
          const current = !isEllipsis && (item.current || isLast)

          return (
            <React.Fragment key={isEllipsis ? `ellipsis-${index}` : `${item.title ?? item.href ?? index}`}>
              {index > 0 ? <BreadcrumbSeparator className="shrink-0" /> : null}
              <BreadcrumbItem className="min-w-0">
                {isEllipsis ? (
                  <BreadcrumbEllipsis className={cn("text-muted-foreground", radiusClassName(radius))} />
                ) : current ? (
                  <BreadcrumbPage
                    title={item.title}
                    className={cn("inline-flex min-w-0 max-w-64 items-center gap-1.5 truncate font-medium", pageClassName(appearance))}
                  >
                    {item.icon ? <span className="shrink-0 text-muted-foreground">{item.icon}</span> : null}
                    <span className="truncate">{item.label}</span>
                  </BreadcrumbPage>
                ) : (
                  <BreadcrumbLink
                    href={item.href}
                    title={item.title}
                    className={cn(
                      "inline-flex min-w-0 max-w-48 items-center gap-1.5 truncate transition-colors",
                      linkClassName(appearance),
                    )}
                    onClick={(event) => {
                      if (item.onClick || onNavigate) {
                        event.preventDefault()
                      }
                      item.onClick?.()
                      onNavigate?.(item)
                    }}
                  >
                    {item.icon ? <span className="shrink-0 text-muted-foreground">{item.icon}</span> : null}
                    <span className="truncate">{item.label}</span>
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

export function filePathToBreadcrumbItems(
  path: string,
  options: {
    rootLabel?: React.ReactNode
    hrefForPath?: (path: string) => string
  } = {},
): LemmaBreadcrumbItem[] {
  const rootLabel = options.rootLabel ?? "Files"
  const normalized = path.trim() || "/"
  const parts = normalized.split("/").filter(Boolean)
  const items: LemmaBreadcrumbItem[] = [
    { label: rootLabel, href: options.hrefForPath?.("/") },
  ]

  let currentPath = ""
  for (const part of parts) {
    currentPath += `/${part}`
    items.push({
      label: part,
      title: currentPath,
      href: options.hrefForPath?.(currentPath),
    })
  }

  if (items.length > 0) items[items.length - 1].current = true
  return items
}

export function recordBreadcrumbItems({
  sectionLabel,
  sectionHref,
  recordLabel,
  recordHref,
}: {
  sectionLabel: React.ReactNode
  sectionHref?: string
  recordLabel: React.ReactNode
  recordHref?: string
}): LemmaBreadcrumbItem[] {
  return [
    { label: sectionLabel, href: sectionHref },
    { label: recordLabel, href: recordHref, current: true },
  ]
}

function collapseBreadcrumbItems(items: LemmaBreadcrumbItem[], maxItems: number): Array<LemmaBreadcrumbItem | null> {
  if (maxItems <= 0 || items.length <= maxItems) return items
  if (maxItems <= 2) return [items[0], null, items[items.length - 1]].filter(Boolean) as Array<LemmaBreadcrumbItem | null>
  const tailCount = Math.max(1, maxItems - 2)
  return [items[0], null, ...items.slice(-tailCount)]
}

function densityClassName(density: LemmaBreadcrumbDensity) {
  if (density === "compact") return "gap-1 text-xs"
  if (density === "spacious") return "gap-2.5 text-sm"
  return "gap-1.5 text-sm"
}

function linkClassName(appearance: LemmaBreadcrumbAppearance) {
  if (appearance === "minimal" || appearance === "borderless") return "text-muted-foreground hover:text-foreground"
  if (appearance === "contained") return "text-muted-foreground hover:text-card-foreground"
  return "text-muted-foreground hover:text-foreground"
}

function pageClassName(appearance: LemmaBreadcrumbAppearance) {
  if (appearance === "contained") return "text-card-foreground"
  return "text-foreground"
}

function radiusClassName(radius: LemmaBreadcrumbRadius) {
  if (radius === "none") return "rounded-none"
  if (radius === "sm") return "rounded-sm"
  if (radius === "md") return "rounded-md"
  if (radius === "xl") return "rounded-xl"
  return "rounded-lg"
}
