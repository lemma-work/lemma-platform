import * as React from "react"
import { LogOut, User, ChevronDown } from "lucide-react"
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuGroup,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/lemma/ui/dropdown-menu"
import { cn } from "@/components/lemma/lib/utils"

export type LemmaUserMenuAppearance = "default" | "borderless" | "minimal" | "contained"
export type LemmaUserMenuDensity = "compact" | "comfortable" | "spacious"
export type LemmaUserMenuRadius = "none" | "sm" | "md" | "lg" | "xl" | "2xl" | "3xl" | "full"

export interface LemmaUserMenuItem {
  label: string
  href?: string
  onClick?: () => void
  icon?: React.ReactNode
  variant?: "default" | "destructive"
}

export interface LemmaUserMenuProps {
  userName?: string
  userEmail?: string
  userAvatarUrl?: string
  isOnline?: boolean
  menuItems?: LemmaUserMenuItem[]
  onSignOut?: () => void
  appearance?: LemmaUserMenuAppearance
  density?: LemmaUserMenuDensity
  radius?: LemmaUserMenuRadius
  className?: string
}

const RADIUS_MAP: Record<LemmaUserMenuRadius, Record<string, string>> = {
  none:  { surface: "rounded-none", control: "rounded-none", pill: "rounded-none", overlay: "rounded-none" },
  sm:    { surface: "rounded-sm", control: "rounded-sm", pill: "rounded-full", overlay: "rounded-sm" },
  md:    { surface: "rounded-md", control: "rounded-md", pill: "rounded-full", overlay: "rounded-md" },
  lg:    { surface: "rounded-lg", control: "rounded-md", pill: "rounded-full", overlay: "rounded-lg" },
  xl:    { surface: "rounded-xl", control: "rounded-lg", pill: "rounded-full", overlay: "rounded-xl" },
  "2xl": { surface: "rounded-2xl", control: "rounded-xl", pill: "rounded-full", overlay: "rounded-2xl" },
  "3xl": { surface: "rounded-3xl", control: "rounded-2xl", pill: "rounded-full", overlay: "rounded-3xl" },
  full:  { surface: "rounded-3xl", control: "rounded-full", pill: "rounded-full", overlay: "rounded-3xl" },
}

function userMenuRadiusClassName(
  radius: LemmaUserMenuRadius,
  kind: "surface" | "control" | "pill" | "overlay",
): string {
  return RADIUS_MAP[radius]?.[kind] ?? RADIUS_MAP.lg[kind]
}

function triggerVariant(appearance: LemmaUserMenuAppearance) {
  return appearance === "minimal" || appearance === "borderless" ? "ghost" : "outline"
}

function avatarShellClassName(appearance: LemmaUserMenuAppearance) {
  return cn(
    "relative flex items-center justify-center bg-muted text-muted-foreground font-medium select-none overflow-hidden",
    appearance === "borderless" ? "ring-0" : "ring-1 ring-border/60",
    appearance === "minimal" ? "ring-0 bg-muted/40" : null,
    appearance === "contained" ? "bg-background" : null,
  )
}

function contentClassName(appearance: LemmaUserMenuAppearance, radius: LemmaUserMenuRadius) {
  return cn(
    "w-64",
    userMenuRadiusClassName(radius, "overlay"),
    appearance === "minimal" ? "border-transparent bg-background/95 shadow-none ring-1 ring-border/15" : null,
    appearance === "borderless" ? "border-transparent shadow-2xl ring-0" : null,
    appearance === "contained" ? "border-border/80 bg-card shadow-xl" : null,
  )
}

function labelPadding(density: LemmaUserMenuDensity) {
  if (density === "compact") return "px-2 py-1.5"
  if (density === "spacious") return "px-3 py-3"
  return "px-3 py-2"
}

function itemPadding(density: LemmaUserMenuDensity) {
  if (density === "compact") return "px-2 py-1.5"
  if (density === "spacious") return "px-3 py-3"
  return "px-2 py-2"
}

function avatarSize(density: LemmaUserMenuDensity) {
  if (density === "compact") return "size-7 text-xs"
  if (density === "spacious") return "size-10 text-base"
  return "size-8 text-sm"
}

function getInitials(name?: string) {
  if (!name) return "U"
  return name.trim().charAt(0).toUpperCase()
}

export function LemmaUserMenu({
  userName,
  userEmail,
  userAvatarUrl,
  isOnline,
  menuItems,
  onSignOut,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  className,
}: LemmaUserMenuProps) {
  const initials = getInitials(userName)

  return (
    <DropdownMenu>
      <DropdownMenuTrigger
        className={cn(
          "inline-flex items-center gap-2 px-2 rounded-md border font-medium transition-colors",
          triggerVariant(appearance) === "ghost" ? "border-transparent bg-transparent hover:bg-muted/40" : "border-border bg-background hover:bg-muted/80",
          appearance === "minimal" ? "border-transparent bg-transparent shadow-none hover:bg-muted/40" : null,
          appearance === "borderless" ? "border-transparent bg-transparent shadow-none" : null,
          userMenuRadiusClassName(radius, "control"),
          density === "compact" ? "h-7 text-xs" : density === "spacious" ? "h-10 text-sm" : "h-8 text-xs",
          className,
        )}
      >
        <span className="relative">
          <span className={cn(avatarShellClassName(appearance), userMenuRadiusClassName(radius, "pill"), avatarSize(density))}>
            {userAvatarUrl ? (
              <img src={userAvatarUrl} alt={userName ?? "User"} className="size-full object-cover" />
            ) : (
              <span>{initials}</span>
            )}
          </span>
          {isOnline ? (
            <span className="absolute bottom-0 right-0 size-2.5 rounded-full border-2 border-background bg-green-500" />
          ) : null}
        </span>
        <ChevronDown className="size-3.5 opacity-50" />
      </DropdownMenuTrigger>

      <DropdownMenuContent align="end" className={contentClassName(appearance, radius)}>
        <DropdownMenuGroup>
          <DropdownMenuLabel className={labelPadding(density)}>
            <div className="flex items-center gap-3">
              <span className="relative">
                <span className={cn(avatarShellClassName(appearance), userMenuRadiusClassName(radius, "pill"), "flex size-9 items-center justify-center text-sm")}>
                  {userAvatarUrl ? (
                    <img src={userAvatarUrl} alt={userName ?? "User"} className="size-full object-cover" />
                  ) : (
                    <span>{initials}</span>
                  )}
                </span>
                {isOnline ? (
                  <span className="absolute bottom-0 right-0 size-2 rounded-full border-2 border-background bg-green-500" />
                ) : null}
              </span>
              <span className="min-w-0 flex-1">
                <p className="truncate text-sm font-medium">{userName ?? "Unknown user"}</p>
                {userEmail ? (
                  <p className="truncate text-xs text-muted-foreground">{userEmail}</p>
                ) : null}
              </span>
            </div>
          </DropdownMenuLabel>
        </DropdownMenuGroup>

        {menuItems && menuItems.length > 0 ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              {menuItems.map((item, index) => (
                <DropdownMenuItem
                  key={index}
                  className={cn(itemPadding(density), item.variant === "destructive" ? "text-destructive focus:text-destructive" : null)}
                  onClick={() => {
                    if (item.onClick) {
                      item.onClick()
                    } else if (item.href) {
                      window.location.assign(item.href)
                    }
                  }}
                >
                  <span className="flex items-center gap-2">
                    {item.icon ?? <User className="size-4" />}
                    {item.label}
                  </span>
                </DropdownMenuItem>
              ))}
            </DropdownMenuGroup>
          </>
        ) : null}

        {onSignOut ? (
          <>
            <DropdownMenuSeparator />
            <DropdownMenuGroup>
              <DropdownMenuItem className={cn(itemPadding(density), "text-destructive focus:text-destructive")} onClick={onSignOut}>
                <span className="flex items-center gap-2">
                  <LogOut className="size-4" />
                  Sign out
                </span>
              </DropdownMenuItem>
            </DropdownMenuGroup>
          </>
        ) : null}
      </DropdownMenuContent>
    </DropdownMenu>
  )
}
