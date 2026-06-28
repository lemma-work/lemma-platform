import * as React from "react"
import { AlertCircle, Check, ChevronsUpDown, Loader2, RefreshCw, Search, ShieldPlus, User, UserPlus, Users, X } from "lucide-react"
import { Badge } from "@/components/lemma/ui/badge"
import { Button } from "@/components/lemma/ui/button"
import { Input } from "@/components/lemma/ui/input"
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from "@/components/lemma/ui/select"
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from "@/components/lemma/ui/popover"
import { Skeleton } from "@/components/lemma/ui/skeleton"
import {
  useAddPodMember,
  useMembers,
  useOrganizationMembers,
  useRemovePodMember,
  useUpdatePodMemberRole,
} from "lemma-sdk/react"
import { PodRole, type LemmaClient, type OrganizationMember, type PodMember } from "lemma-sdk"
import { cn } from "@/components/lemma/lib/utils"

export type LemmaMemberAppearance = "default" | "minimal" | "borderless" | "contained"
export type LemmaMemberDensity = "compact" | "comfortable" | "spacious"
export type LemmaMemberRadius = "none" | "sm" | "md" | "lg" | "xl"
export type LemmaMemberChipSize = "sm" | "md" | "lg"

export interface LemmaMembersProps {
  client: LemmaClient
  podId?: string
  organizationId?: string
  enabled?: boolean
  title?: React.ReactNode
  description?: React.ReactNode
  searchPlaceholder?: string
  allowRoleEdit?: boolean
  allowRemove?: boolean
  allowAdd?: boolean
  defaultAddRole?: PodRole
  currentUserId?: string | null
  appearance?: LemmaMemberAppearance
  density?: LemmaMemberDensity
  radius?: LemmaMemberRadius
  className?: string
}

export interface LemmaMemberChipProps {
  member?: PodMember | null
  userId?: string | null
  label?: React.ReactNode
  email?: string | null
  role?: React.ReactNode
  avatarUrl?: string | null
  size?: LemmaMemberChipSize
  appearance?: LemmaMemberAppearance
  radius?: LemmaMemberRadius
  className?: string
}

export interface LemmaAvatarGroupProps {
  members: Array<PodMember | null | undefined>
  max?: number
  size?: LemmaMemberChipSize
  radius?: LemmaMemberRadius
  className?: string
}

export interface LemmaMemberSelectProps {
  client: LemmaClient
  podId?: string
  value?: string | null
  onValueChange?: (userId: string | null, member: PodMember | null) => void
  enabled?: boolean
  placeholder?: string
  searchPlaceholder?: string
  clearable?: boolean
  appearance?: LemmaMemberAppearance
  density?: LemmaMemberDensity
  radius?: LemmaMemberRadius
  className?: string
}

export interface LemmaUserFieldProps {
  client: LemmaClient
  podId?: string
  userId?: string | null
  enabled?: boolean
  fallback?: React.ReactNode
  appearance?: LemmaMemberAppearance
  radius?: LemmaMemberRadius
  size?: LemmaMemberChipSize
  className?: string
}

export function LemmaMembers({
  client,
  podId,
  organizationId,
  enabled = true,
  title = "Members",
  description = "People who have access to this pod.",
  searchPlaceholder = "Search members...",
  allowRoleEdit = false,
  allowRemove = false,
  allowAdd = false,
  defaultAddRole = PodRole.POD_USER,
  currentUserId,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  className,
}: LemmaMembersProps) {
  const membersState = useMembers({ client, podId, enabled })
  const organizationMembersState = useOrganizationMembers({
    client,
    organizationId: organizationId ?? "",
    enabled: enabled && !!organizationId,
  })

  const resolvedPodId = podId ?? client.podId ?? null
  const [query, setQuery] = React.useState("")
  const [addRole, setAddRole] = React.useState<PodRole>(defaultAddRole)
  const [actionKey, setActionKey] = React.useState<string | null>(null)
  const [actionError, setActionError] = React.useState<string | null>(null)

  const addPodMember = useAddPodMember({
    client,
    podId,
    enabled: enabled && allowAdd,
    defaultRole: addRole,
    onError: (error) => {
      setActionError(error instanceof Error ? error.message : "Failed to add member.")
    },
  })

  const updatePodMemberRole = useUpdatePodMemberRole({
    client,
    podId,
    enabled: enabled && allowRoleEdit,
    onError: (error) => {
      setActionError(error instanceof Error ? error.message : "Failed to update member role.")
    },
  })

  const removePodMember = useRemovePodMember({
    client,
    podId,
    enabled: enabled && allowRemove,
    onError: (error) => {
      setActionError(error instanceof Error ? error.message : "Failed to remove member.")
    },
  })

  React.useEffect(() => {
    setAddRole(defaultAddRole)
  }, [defaultAddRole])

  const filteredMembers = React.useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return membersState.members
    return membersState.members.filter((member) => {
      const haystack = [member.user_name, member.user_email, member.user_id, member.role].filter(Boolean).join(" ").toLowerCase()
      return haystack.includes(needle)
    })
  }, [membersState.members, query])

  const addableOrganizationMembers = React.useMemo(() => {
    const existingUserIds = new Set(membersState.members.map((member) => member.user_id))
    const candidates = organizationMembersState.members.filter((member) => !existingUserIds.has(member.user_id))
    const needle = query.trim().toLowerCase()
    if (!needle) return candidates
    return candidates.filter((member) => {
      const haystack = [
        organizationMemberLabel(member),
        member.user?.email,
        member.user_id,
        member.role,
      ].filter(Boolean).join(" ").toLowerCase()
      return haystack.includes(needle)
    })
  }, [membersState.members, organizationMembersState.members, query])

  const refreshAll = React.useCallback(async () => {
    await Promise.all([
      membersState.refresh(),
      organizationId && allowAdd ? organizationMembersState.refresh() : Promise.resolve([]),
    ])
  }, [allowAdd, membersState, organizationId, organizationMembersState])

  const handleRoleChange = React.useCallback(async (member: PodMember, nextRole: PodRole) => {
    if (!resolvedPodId || member.role === nextRole) return
    setActionKey(`role:${member.pod_member_id}`)
    setActionError(null)
    try {
      const updated = await updatePodMemberRole.updateRole(nextRole, {
        memberId: member.pod_member_id,
      })
      if (updated) {
        await membersState.refresh()
      }
    } finally {
      setActionKey(null)
    }
  }, [membersState, resolvedPodId, updatePodMemberRole])

  const handleRemove = React.useCallback(async (member: PodMember) => {
    if (!resolvedPodId) return
    setActionKey(`remove:${member.pod_member_id}`)
    setActionError(null)
    try {
      const removed = await removePodMember.remove({ memberId: member.pod_member_id })
      if (removed) {
        await refreshAll()
      }
    } finally {
      setActionKey(null)
    }
  }, [refreshAll, removePodMember, resolvedPodId])

  const handleAdd = React.useCallback(async (member: OrganizationMember) => {
    if (!resolvedPodId) return
    setActionKey(`add:${member.id}`)
    setActionError(null)
    try {
      const added = await addPodMember.add({
        organizationMemberId: member.id,
        role: addRole,
      })
      if (added) {
        await refreshAll()
      }
    } finally {
      setActionKey(null)
    }
  }, [addPodMember, addRole, refreshAll, resolvedPodId])

  const isLoading = membersState.isLoading || (!!organizationId && allowAdd && organizationMembersState.isLoading)
  const isUnavailable = !resolvedPodId

  return (
    <section
      data-appearance={appearance}
      data-density={density}
      data-radius={radius}
      className={cn(
        "lemma-members flex min-h-0 flex-col gap-4",
        className,
      )}
    >
      <div className={cn("border", chipClassName(appearance), radiusClassName(radius, "surface"), density === "compact" ? "p-4" : density === "spacious" ? "p-6" : "p-5")}>
        <div className="flex flex-col gap-4 lg:flex-row lg:items-start lg:justify-between">
          <div className="space-y-2">
            <div className="flex items-center gap-2">
              <span className={cn("flex size-9 items-center justify-center bg-muted text-muted-foreground", radiusClassName(radius, "pill"))}>
                <Users className="size-4" />
              </span>
              <div>
                <h2 className="text-lg font-semibold tracking-tight text-foreground">{title}</h2>
                <p className="text-sm text-muted-foreground">{description}</p>
              </div>
            </div>
            <div className="flex flex-wrap items-center gap-2 text-xs text-muted-foreground">
              <Badge variant="secondary">{membersState.total || membersState.members.length} pod members</Badge>
              {organizationId ? (
                <Badge variant="secondary">{organizationMembersState.total || organizationMembersState.members.length} org members</Badge>
              ) : null}
            </div>
          </div>
          <div className="flex w-full flex-col gap-2 lg:w-auto lg:min-w-80">
            <div className="relative">
              <Search className="absolute left-3 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
              <Input
                value={query}
                onChange={(event) => setQuery(event.target.value)}
                placeholder={searchPlaceholder}
                className={cn("pl-9", triggerClassName(appearance, density, radius))}
              />
            </div>
            <div className="flex flex-wrap items-center justify-end gap-2">
              <Button variant="outline" size="sm" className={radiusClassName(radius, "control")} onClick={() => void refreshAll()}>
                <RefreshCw className="size-3.5" />
                Refresh
              </Button>
            </div>
          </div>
        </div>
      </div>

      {actionError ? (
        <div className={cn("flex items-start gap-2 border border-destructive/30 bg-destructive/10 px-3 py-2 text-sm text-destructive", radiusClassName(radius, "surface"))}>
          <AlertCircle className="mt-0.5 size-4 shrink-0" />
          <span>{actionError}</span>
        </div>
      ) : null}

      {isUnavailable ? (
        <EmptyMembersState
          icon={ShieldPlus}
          title="Pod context is required"
          description="Pass podId or use a pod-scoped client to manage members."
          appearance={appearance}
          radius={radius}
        />
      ) : isLoading ? (
        <MembersWorkspaceSkeleton appearance={appearance} radius={radius} />
      ) : membersState.error ? (
        <EmptyMembersState
          icon={AlertCircle}
          title="Failed to load members"
          description={membersState.error.message}
          appearance={appearance}
          radius={radius}
        />
      ) : (
        <div className={cn("grid gap-4", organizationId && allowAdd ? "xl:grid-cols-[minmax(0,1.7fr)_minmax(20rem,1fr)]" : "grid-cols-1")}>
          <div className={cn("border", chipClassName(appearance), radiusClassName(radius, "surface"))}>
            <div className="flex items-center justify-between gap-3 border-b border-border/40 px-4 py-3">
              <div>
                <p className="text-sm font-medium text-foreground">Pod Members</p>
                <p className="text-xs text-muted-foreground">People who can access and operate this pod.</p>
              </div>
              <Badge variant="secondary">{filteredMembers.length}</Badge>
            </div>
            <div className="divide-y divide-border/30">
              {filteredMembers.length === 0 ? (
                <EmptyMembersState
                  icon={Users}
                  title="No members found"
                  description={query ? "Try a broader search term." : "Add people from the organization to get started."}
                  appearance={appearance}
                  radius={radius}
                  compact
                />
              ) : (
                filteredMembers.map((member) => {
                  const roleActionKey = `role:${member.pod_member_id}`
                  const removeActionKey = `remove:${member.pod_member_id}`
                  const isRoleLoading = actionKey === roleActionKey
                  const isRemoveLoading = actionKey === removeActionKey
                  const canRemove = allowRemove && member.user_id !== currentUserId

                  return (
                    <div key={member.pod_member_id} className={cn("flex flex-col gap-3 px-4 py-3 md:flex-row md:items-center md:justify-between", density === "compact" ? "md:py-2.5" : null)}>
                      <div className="min-w-0">
                        <LemmaMemberChip member={member} appearance="minimal" radius={radius} size="lg" />
                      </div>
                      <div className="flex flex-wrap items-center gap-2">
                        {allowRoleEdit ? (
                          <PodRoleSelect
                            value={member.role}
                            disabled={isRoleLoading}
                            radius={radius}
                            onValueChange={(nextRole) => void handleRoleChange(member, nextRole)}
                          />
                        ) : (
                          <Badge variant="secondary">{formatPodRole(member.role)}</Badge>
                        )}
                        {canRemove ? (
                          <Button
                            variant="outline"
                            size="sm"
                            disabled={isRemoveLoading}
                            className={cn("text-destructive hover:text-destructive", radiusClassName(radius, "control"))}
                            onClick={() => void handleRemove(member)}
                          >
                            {isRemoveLoading ? <Loader2 className="size-3.5 animate-spin" /> : <X className="size-3.5" />}
                            Remove
                          </Button>
                        ) : currentUserId && member.user_id === currentUserId ? (
                          <Badge variant="secondary">You</Badge>
                        ) : null}
                      </div>
                    </div>
                  )
                })
              )}
            </div>
          </div>

          {organizationId && allowAdd ? (
            <div className={cn("border", chipClassName(appearance), radiusClassName(radius, "surface"))}>
              <div className="flex items-center justify-between gap-3 border-b border-border/40 px-4 py-3">
                <div>
                  <p className="text-sm font-medium text-foreground">Add From Organization</p>
                  <p className="text-xs text-muted-foreground">Bring existing organization members into this pod.</p>
                </div>
                <Badge variant="secondary">{addableOrganizationMembers.length}</Badge>
              </div>
              <div className="space-y-4 p-4">
                <div className="space-y-2">
                  <p className="text-xs font-semibold uppercase tracking-widest text-muted-foreground">New member role</p>
                  <PodRoleSelect value={addRole} radius={radius} onValueChange={setAddRole} />
                </div>

                {organizationMembersState.error ? (
                  <div className="text-sm text-destructive">{organizationMembersState.error.message}</div>
                ) : addableOrganizationMembers.length === 0 ? (
                  <EmptyMembersState
                    icon={UserPlus}
                    title="No addable members"
                    description={query ? "Try a broader search term." : "Everyone in the organization is already in this pod."}
                    appearance={appearance}
                    radius={radius}
                    compact
                  />
                ) : (
                  <div className="flex flex-col gap-2">
                    {addableOrganizationMembers.slice(0, 8).map((member) => {
                      const isAdding = actionKey === `add:${member.id}`
                      return (
                        <div key={member.id} className={cn("flex items-center justify-between gap-3 border border-border/40 bg-muted/15 px-3 py-2", radiusClassName(radius, "control"))}>
                          <div className="min-w-0">
                            <p className="truncate text-sm font-medium text-foreground">{organizationMemberLabel(member)}</p>
                            <p className="truncate text-xs text-muted-foreground">{member.user?.email ?? member.user_id}</p>
                          </div>
                          <Button
                            size="sm"
                            disabled={isAdding}
                            className={radiusClassName(radius, "control")}
                            onClick={() => void handleAdd(member)}
                          >
                            {isAdding ? <Loader2 className="size-3.5 animate-spin" /> : <UserPlus className="size-3.5" />}
                            Add
                          </Button>
                        </div>
                      )
                    })}
                    {addableOrganizationMembers.length > 8 ? (
                      <p className="text-xs text-muted-foreground">
                        Showing 8 of {addableOrganizationMembers.length} available organization members.
                      </p>
                    ) : null}
                  </div>
                )}
              </div>
            </div>
          ) : null}
        </div>
      )}
    </section>
  )
}

export function LemmaMemberChip({
  member,
  userId,
  label,
  email,
  role,
  avatarUrl,
  size = "md",
  appearance = "default",
  radius = "lg",
  className,
}: LemmaMemberChipProps) {
  const name = label ?? memberLabel(member) ?? userId ?? "Unknown user"
  const resolvedEmail = email ?? member?.user_email
  const resolvedRole = role ?? member?.role
  const initials = getInitials(String(name))

  return (
    <span className={cn("inline-flex min-w-0 items-center gap-2 border font-medium", chipClassName(appearance), chipSizeClassName(size), radiusClassName(radius, "pill"), className)}>
      <span className={cn("flex shrink-0 items-center justify-center overflow-hidden bg-muted text-muted-foreground", avatarSizeClassName(size), radiusClassName(radius, "pill"))}>
        {avatarUrl ? <img src={avatarUrl} alt={String(name)} className="size-full object-cover" /> : <span>{initials}</span>}
      </span>
      <span className="min-w-0">
        <span className="block truncate">{name}</span>
        {resolvedEmail && size !== "sm" ? <span className="block truncate text-[10px] font-normal text-muted-foreground">{resolvedEmail}</span> : null}
      </span>
      {resolvedRole ? <Badge variant="secondary" className="shrink-0 text-[10px]">{resolvedRole}</Badge> : null}
    </span>
  )
}

export function LemmaAvatarGroup({
  members,
  max = 4,
  size = "md",
  radius = "lg",
  className,
}: LemmaAvatarGroupProps) {
  const visibleMembers = members.filter(Boolean).slice(0, max) as PodMember[]
  const extraCount = Math.max(0, members.filter(Boolean).length - visibleMembers.length)

  return (
    <div className={cn("flex items-center", className)}>
      {visibleMembers.map((member, index) => (
        <span
          key={member.user_id}
          title={memberLabel(member)}
          className={cn(
            "flex shrink-0 items-center justify-center border-2 border-background bg-muted text-muted-foreground ring-1 ring-border/50",
            avatarSizeClassName(size),
            radiusClassName(radius, "pill"),
            index > 0 ? "-ml-2" : null,
          )}
        >
          {getInitials(memberLabel(member))}
        </span>
      ))}
      {extraCount > 0 ? (
        <span className={cn("-ml-2 flex shrink-0 items-center justify-center border-2 border-background bg-muted text-[10px] font-medium text-muted-foreground ring-1 ring-border/50", avatarSizeClassName(size), radiusClassName(radius, "pill"))}>
          +{extraCount}
        </span>
      ) : null}
    </div>
  )
}

export function LemmaMemberSelect({
  client,
  podId,
  value,
  onValueChange,
  enabled = true,
  placeholder = "Select member",
  searchPlaceholder = "Search members...",
  clearable = true,
  appearance = "default",
  density = "comfortable",
  radius = "lg",
  className,
}: LemmaMemberSelectProps) {
  const [open, setOpen] = React.useState(false)
  const [query, setQuery] = React.useState("")
  const membersState = useMembers({ client, podId, enabled })
  const selectedMember = React.useMemo(
    () => membersState.members.find((member) => member.user_id === value) ?? null,
    [membersState.members, value],
  )
  const filteredMembers = React.useMemo(() => {
    const needle = query.trim().toLowerCase()
    if (!needle) return membersState.members
    return membersState.members.filter((member) => {
      const haystack = [member.user_name, member.user_email, member.user_id, member.role].filter(Boolean).join(" ").toLowerCase()
      return haystack.includes(needle)
    })
  }, [membersState.members, query])

  const selectMember = React.useCallback((member: PodMember | null) => {
    onValueChange?.(member?.user_id ?? null, member)
    setOpen(false)
    setQuery("")
  }, [onValueChange])

  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger
        type="button"
        className={cn(
          "inline-flex min-w-52 items-center justify-between gap-3 border text-sm font-medium transition-colors disabled:pointer-events-none disabled:opacity-50",
          appearance === "minimal" || appearance === "borderless" ? "border-transparent bg-transparent hover:bg-muted" : "border-border bg-background hover:bg-muted",
          triggerClassName(appearance, density, radius),
          className,
        )}
        disabled={!enabled}
      >
        <span className="min-w-0 flex-1 text-left">
          {selectedMember ? (
            <span className="flex min-w-0 items-center gap-2">
              <span className={cn("flex shrink-0 items-center justify-center bg-muted text-[10px] text-muted-foreground", radiusClassName(radius, "pill"), "size-5")}>
                {getInitials(memberLabel(selectedMember))}
              </span>
              <span className="truncate">{memberLabel(selectedMember)}</span>
            </span>
          ) : (
            <span className="text-muted-foreground">{placeholder}</span>
          )}
        </span>
        <ChevronsUpDown className="size-3.5 shrink-0 text-muted-foreground" />
      </PopoverTrigger>
      <PopoverContent align="start" className={cn("w-72 p-0", popoverClassName(appearance, radius))}>
        <div className="border-b border-border/40 p-2">
          <div className="relative">
            <Search className="absolute left-2.5 top-1/2 size-3.5 -translate-y-1/2 text-muted-foreground" />
            <Input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={searchPlaceholder}
              className={cn("h-8 pl-8 text-xs", radiusClassName(radius, "control"))}
            />
          </div>
        </div>

        <div className="max-h-72 overflow-auto p-1">
          {membersState.isLoading ? (
            <div className="flex flex-col gap-2 p-2">
              {Array.from({ length: 4 }).map((_, index) => (
                <div key={index} className="flex items-center gap-2">
                  <Skeleton className="size-7 rounded-full" />
                  <Skeleton className="h-4 flex-1" />
                </div>
              ))}
            </div>
          ) : membersState.error ? (
            <div className="p-3 text-sm text-destructive">{membersState.error.message}</div>
          ) : filteredMembers.length === 0 ? (
            <div className="flex min-h-24 flex-col items-center justify-center gap-2 text-center text-sm text-muted-foreground">
              <User className="size-5" />
              No members found
            </div>
          ) : (
            <div className="flex flex-col gap-1">
              {clearable && value ? (
                <button
                  type="button"
                  className={cn("flex w-full items-center gap-2 px-2 py-2 text-left text-sm text-muted-foreground hover:bg-muted/45", radiusClassName(radius, "control"))}
                  onClick={() => selectMember(null)}
                >
                  <X className="size-4" />
                  Clear selection
                </button>
              ) : null}
              {filteredMembers.map((member) => {
                const selected = member.user_id === value
                return (
                  <button
                    key={member.user_id}
                    type="button"
                    className={cn("flex w-full items-center gap-2 px-2 py-2 text-left text-sm hover:bg-muted/45", radiusClassName(radius, "control"), selected ? "bg-muted/60" : null)}
                    onClick={() => selectMember(member)}
                  >
                    <span className={cn("flex size-7 shrink-0 items-center justify-center bg-muted text-xs text-muted-foreground", radiusClassName(radius, "pill"))}>
                      {getInitials(memberLabel(member))}
                    </span>
                    <span className="min-w-0 flex-1">
                      <span className="block truncate font-medium text-foreground">{memberLabel(member)}</span>
                      <span className="block truncate text-xs text-muted-foreground">{member.user_email}</span>
                    </span>
                    {selected ? <Check className="size-4 text-primary" /> : null}
                  </button>
                )
              })}
            </div>
          )}
        </div>
      </PopoverContent>
    </Popover>
  )
}

export function LemmaUserField({
  client,
  podId,
  userId,
  enabled = true,
  fallback,
  appearance = "default",
  radius = "lg",
  size = "md",
  className,
}: LemmaUserFieldProps) {
  const membersState = useMembers({ client, podId, enabled: enabled && !!userId })
  const member = React.useMemo(
    () => membersState.members.find((candidate) => candidate.user_id === userId) ?? null,
    [membersState.members, userId],
  )

  if (!userId) return <>{fallback ?? <span className="text-muted-foreground">Unassigned</span>}</>
  if (membersState.isLoading) return <Skeleton className="h-7 w-28 rounded-full" />

  return (
    <LemmaMemberChip
      member={member}
      userId={userId}
      label={member ? undefined : fallback ?? shortId(userId)}
      appearance={appearance}
      radius={radius}
      size={size}
      className={className}
    />
  )
}

export function memberLabel(member?: PodMember | null) {
  if (!member) return ""
  return member.user_name || member.user_email || member.user_id
}

function organizationMemberLabel(member: OrganizationMember) {
  const firstName = member.user?.first_name?.trim()
  const lastName = member.user?.last_name?.trim()
  const fullName = [firstName, lastName].filter(Boolean).join(" ").trim()
  return fullName || member.user?.email || member.user_id
}

function formatPodRole(role: PodRole) {
  if (role === PodRole.POD_ADMIN) return "Admin"
  if (role === PodRole.POD_EDITOR) return "Editor"
  if (role === PodRole.POD_VIEWER) return "Viewer"
  return "Member"
}

function PodRoleSelect({
  value,
  radius,
  disabled,
  onValueChange,
}: {
  value: PodRole
  radius: LemmaMemberRadius
  disabled?: boolean
  onValueChange: (role: PodRole) => void
}) {
  return (
    <Select value={value} onValueChange={(nextRole) => onValueChange(nextRole as PodRole)} disabled={disabled}>
      <SelectTrigger className={cn("min-w-32", radiusClassName(radius, "control"))}>
        <SelectValue />
      </SelectTrigger>
      <SelectContent>
        <SelectItem value={PodRole.POD_ADMIN}>Admin</SelectItem>
        <SelectItem value={PodRole.POD_EDITOR}>Editor</SelectItem>
        <SelectItem value={PodRole.POD_USER}>Member</SelectItem>
        <SelectItem value={PodRole.POD_VIEWER}>Viewer</SelectItem>
      </SelectContent>
    </Select>
  )
}

function EmptyMembersState({
  icon: Icon,
  title,
  description,
  appearance,
  radius,
  compact = false,
}: {
  icon: React.ComponentType<{ className?: string }>
  title: string
  description: string
  appearance: LemmaMemberAppearance
  radius: LemmaMemberRadius
  compact?: boolean
}) {
  return (
    <div className={cn(
      "flex flex-col items-center justify-center gap-3 px-6 text-center",
      compact ? "min-h-40 py-6" : "min-h-64 py-10",
      appearance === "minimal" || appearance === "borderless" ? "bg-transparent" : "bg-muted/15",
      radiusClassName(radius, "surface"),
    )}>
      <span className={cn("flex size-10 items-center justify-center bg-muted text-muted-foreground", radiusClassName(radius, "pill"))}>
        <Icon className="size-4" />
      </span>
      <div>
        <p className="font-medium text-foreground">{title}</p>
        <p className="mt-1 max-w-md text-sm text-muted-foreground">{description}</p>
      </div>
    </div>
  )
}

function MembersWorkspaceSkeleton({
  appearance,
  radius,
}: {
  appearance: LemmaMemberAppearance
  radius: LemmaMemberRadius
}) {
  return (
    <div className={cn("border p-4", chipClassName(appearance), radiusClassName(radius, "surface"))}>
      <div className="space-y-3">
        {Array.from({ length: 5 }).map((_, index) => (
          <div key={index} className="flex items-center justify-between gap-3">
            <div className="flex items-center gap-3">
              <Skeleton className="size-9 rounded-full" />
              <div className="space-y-2">
                <Skeleton className="h-4 w-36" />
                <Skeleton className="h-3 w-48" />
              </div>
            </div>
            <div className="flex items-center gap-2">
              <Skeleton className="h-9 w-28" />
              <Skeleton className="h-9 w-20" />
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

function getInitials(value: string) {
  const parts = value.trim().split(/[\s._-]+/).filter(Boolean)
  if (parts.length === 0) return "?"
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase()
  return `${parts[0][0]}${parts[parts.length - 1][0]}`.toUpperCase()
}

function shortId(value: string) {
  return value.length > 14 ? `${value.slice(0, 8)}...${value.slice(-4)}` : value
}

function chipClassName(appearance: LemmaMemberAppearance) {
  if (appearance === "minimal") return "border-transparent bg-muted/35 text-foreground"
  if (appearance === "borderless") return "border-transparent bg-transparent text-foreground"
  if (appearance === "contained") return "border-border/60 bg-card text-card-foreground"
  return "border-border/50 bg-background text-foreground"
}

function chipSizeClassName(size: LemmaMemberChipSize) {
  if (size === "sm") return "h-6 max-w-44 px-1.5 text-xs"
  if (size === "lg") return "min-h-10 max-w-72 px-2.5 py-1.5 text-sm"
  return "h-8 max-w-60 px-2 text-xs"
}

function avatarSizeClassName(size: LemmaMemberChipSize) {
  if (size === "sm") return "size-4 text-[9px]"
  if (size === "lg") return "size-8 text-sm"
  return "size-6 text-xs"
}

function triggerClassName(
  appearance: LemmaMemberAppearance,
  density: LemmaMemberDensity,
  radius: LemmaMemberRadius,
) {
  return cn(
    density === "compact" ? "h-8 px-2 text-xs" : density === "spacious" ? "h-11 px-3 text-sm" : "h-9 px-2.5 text-sm",
    radiusClassName(radius, "control"),
    appearance === "minimal" ? "border-transparent bg-transparent shadow-none hover:bg-muted/40" : null,
    appearance === "borderless" ? "border-transparent bg-transparent shadow-none" : null,
    appearance === "contained" ? "border-border/70 bg-card" : null,
  )
}

function popoverClassName(appearance: LemmaMemberAppearance, radius: LemmaMemberRadius) {
  return cn(
    radiusClassName(radius, "surface"),
    appearance === "minimal" ? "border-transparent bg-background/95 shadow-none ring-1 ring-border/15" : null,
    appearance === "borderless" ? "border-transparent shadow-2xl ring-0" : null,
    appearance === "contained" ? "border-border/80 bg-card shadow-xl" : null,
  )
}

function radiusClassName(radius: LemmaMemberRadius, target: "surface" | "control" | "pill") {
  if (radius === "none") return "rounded-none"
  if (radius === "sm") return target === "surface" ? "rounded-md" : "rounded-sm"
  if (radius === "md") return target === "surface" ? "rounded-lg" : "rounded-md"
  if (radius === "xl") return target === "pill" ? "rounded-full" : target === "control" ? "rounded-xl" : "rounded-2xl"
  return target === "pill" ? "rounded-full" : target === "control" ? "rounded-lg" : "rounded-xl"
}
