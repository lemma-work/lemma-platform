'use client';

import { use, useMemo, useState } from 'react';
import { ArrowRight, CheckCircle2, Loader2, Mail, Plus, ShieldCheck, UserPlus, Users } from 'lucide-react';
import { toast } from 'sonner';

import { ProtectedRoute } from '@/components/auth/protected-route';
import { PodSettingsShell } from '@/components/pod/pod-settings-shell';
import { ResourceMetricButton, ResourceMetricStrip } from '@/components/pod/resource-layout';
import { DestructiveConfirmationDialog } from '@/components/shared/destructive-confirmation-dialog';
import { EmptyState, QuietEmptyState } from '@/components/shared/empty-state';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { Avatar, AvatarFallback, AvatarImage } from '@/components/ui/avatar';
import { Button } from '@/components/ui/button';
import { Checkbox } from '@/components/ui/checkbox';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from '@/components/ui/select';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { useInviteMember, useOrganizationInvitations, useOrganizationMembers, useRevokeInvitation } from '@/lib/hooks/use-organizations';
import { useApps } from '@/lib/hooks/use-app';
import { useApprovePodJoinRequest, usePodJoinRequests } from '@/lib/hooks/use-pod-join-requests';
import {
    usePodMembers,
    useAddPodMember,
    useRemovePodMember,
    useUpdatePodMemberRoles,
    usePodRoles,
    usePodPermissionCatalog,
    useCreatePodRole,
    useUpdatePodRole,
    useDeletePodRole,
} from '@/lib/hooks/use-pod-members';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePod } from '@/lib/hooks/use-pods';
import { useProfile } from '@/lib/hooks/use-user';
import { OrganizationRole, PodRole } from '@/lib/types';
import { buildPodInviteRedirectUri, getPodInviteRedirectOptions } from '@/lib/utils/invite-redirects';

type AccessView = 'people' | 'invites' | 'requests' | 'roles' | 'available';

export default function PodMembersPage({ params }: { params: Promise<{ id: string }> }) {
    return (
        <ProtectedRoute>
            <PodMembersPageContent params={params} />
        </ProtectedRoute>
    );
}

function PodMembersPageContent({ params }: { params: Promise<{ id: string }> }) {
    const { id: podId } = use(params);
    const podAccess = usePodAccess(podId);
    const { data: pod } = usePod(podId);
    const { data: profile } = useProfile();
    const { data: membersData, isLoading: loadingMembers } = usePodMembers(podId);
    const { data: apps = [] } = useApps(podId);
    const { data: orgMembersData } = useOrganizationMembers(pod?.organization_id || '');
    const { data: invitationsData, isLoading: loadingInvitations } = useOrganizationInvitations(pod?.organization_id || '');
    const {
        data: joinRequestsData,
        isLoading: loadingJoinRequests,
        isError: isJoinRequestsError,
        error: joinRequestsError,
        refetch: refetchJoinRequests,
    } = usePodJoinRequests(podId, 'PENDING');

    const members = membersData?.items || [];
    const orgMembers = orgMembersData?.items || [];
    const pendingPodInvitations = (invitationsData?.items || []).filter((invite) => invite.pod_id === podId);
    const pendingJoinRequests = joinRequestsData?.items || [];
    const podName = pod?.name || 'this pod';
    const currentOrgRole = orgMembers.find((member) => member.user_id === profile?.id)?.role;
    const canManageMembers = podAccess.can('pod.member.manage');
    const canManageRoles = podAccess.can('pod.role.manage');
    const canInviteByEmail =
        canManageMembers &&
        (
            currentOrgRole === OrganizationRole.ORG_OWNER ||
            currentOrgRole === OrganizationRole.ORG_EDITOR
        );

    const { mutate: removeMember, isPending: isRemoving } = useRemovePodMember(podId);
    const { mutate: updateRoles, isPending: isUpdatingRoles } = useUpdatePodMemberRoles(podId);
    const { mutate: addMember, isPending: isAdding } = useAddPodMember(podId);
    const { data: rolesData } = usePodRoles(podId);
    const { data: permissionCatalogData } = usePodPermissionCatalog(podId);
    const { mutate: approveJoinRequest, isPending: isApprovingJoinRequest } = useApprovePodJoinRequest(podId);
    const { mutate: inviteMember, isPending: isInviting } = useInviteMember(pod?.organization_id || '');
    const { mutate: revokeInvitation, isPending: isRevokingInvitation } = useRevokeInvitation(pod?.organization_id || '');
    const { mutate: createPodRole, isPending: isCreatingRole } = useCreatePodRole(podId);
    const { mutate: updatePodRole, isPending: isSavingRole } = useUpdatePodRole(podId);
    const { mutate: deletePodRole, isPending: isDeletingRole } = useDeletePodRole(podId);

    const [addDialogOpen, setAddDialogOpen] = useState(false);
    const [createRoleDialogOpen, setCreateRoleDialogOpen] = useState(false);
    const [selectedOrgMemberId, setSelectedOrgMemberId] = useState('');
    const [selectedRole, setSelectedRole] = useState<PodRole>(PodRole.POD_USER);
    const [inviteEmail, setInviteEmail] = useState('');
    const [inviteOrgRole, setInviteOrgRole] = useState<OrganizationRole>(OrganizationRole.ORG_MEMBER);
    const [invitePodRole, setInvitePodRole] = useState<PodRole>(PodRole.POD_USER);
    const [selectedInviteRedirectUri, setSelectedInviteRedirectUri] = useState<string | null>(null);
    const [revokingInvitationId, setRevokingInvitationId] = useState<string | null>(null);
    const [memberPendingRemove, setMemberPendingRemove] = useState<{ id: string; label: string } | null>(null);
    const [invitationPendingRevoke, setInvitationPendingRevoke] = useState<{ id: string; email: string } | null>(null);
    const [approvingRequestId, setApprovingRequestId] = useState<string | null>(null);
    const [activeView, setActiveView] = useState<AccessView>('people');
    const [editingRoleName, setEditingRoleName] = useState<string | null>(null);
    const [newRoleName, setNewRoleName] = useState('');
    const [newRoleDescription, setNewRoleDescription] = useState('');
    const [newRoleTemplate, setNewRoleTemplate] = useState<string>(PodRole.POD_VIEWER);
    const [approvalConfigByRequestId, setApprovalConfigByRequestId] = useState<
        Record<string, { orgRole: OrganizationRole; podRole: PodRole }>
    >({});

    const availableMembers = orgMembers.filter((orgMember) => !members.some((podMember) => podMember.user_id === orgMember.user_id));
    const roles = useMemo(() => rolesData?.items || [], [rolesData?.items]);
    const permissionCatalog = useMemo(() => permissionCatalogData?.items || [], [permissionCatalogData?.items]);
    const roleNames = useMemo(() => roles.map((role) => role.name), [roles]);
    const roleMetaByName = useMemo(() => new Map(roles.map((role) => [role.name, role])), [roles]);
    const activeEditingRoleName = editingRoleName || roles[0]?.name || null;
    const editingRole = roles.find((role) => role.name === activeEditingRoleName) || null;
    const groupedPermissions = useMemo(() => groupPermissionCatalog(permissionCatalog), [permissionCatalog]);
    const defaultInviteRedirectUri = useMemo(
        () => buildPodInviteRedirectUri({
            podId,
            podRole: invitePodRole,
            apps,
        }),
        [apps, invitePodRole, podId]
    );
    const redirectOptions = useMemo(
        () => getPodInviteRedirectOptions({ podId, apps }),
        [apps, podId]
    );
    const inviteRedirectUri = redirectOptions.some((option) => option.value === selectedInviteRedirectUri)
        ? selectedInviteRedirectUri as string
        : defaultInviteRedirectUri;

    const resolveApprovalConfig = (requestId: string) =>
        approvalConfigByRequestId[requestId] || {
            orgRole: OrganizationRole.ORG_MEMBER,
            podRole: PodRole.POD_USER,
        };

    const setApprovalConfig = (
        requestId: string,
        next: Partial<{ orgRole: OrganizationRole; podRole: PodRole }>
    ) => {
        setApprovalConfigByRequestId((current) => {
            const previous = current[requestId] || {
                orgRole: OrganizationRole.ORG_MEMBER,
                podRole: PodRole.POD_USER,
            };

            return {
                ...current,
                [requestId]: {
                    ...previous,
                    ...next,
                },
            };
        });
    };

    const handleAddMember = () => {
        if (!canManageMembers || !selectedOrgMemberId) return;

        addMember(
            { organization_member_id: selectedOrgMemberId, role: selectedRole },
            {
                onSuccess: () => {
                    toast.success('Member added to pod');
                    setAddDialogOpen(false);
                    setSelectedOrgMemberId('');
                    setSelectedRole(PodRole.POD_USER);
                },
                onError: (err) => toast.error(`Failed to add member: ${err.message}`),
            }
        );
    };

    const handleInviteByEmail = () => {
        const email = inviteEmail.trim();
        if (!email || !pod?.organization_id) return;
        if (!canInviteByEmail) {
            toast.error('Only organization owners and editors can invite new people by email.');
            return;
        }

        inviteMember(
            {
                email,
                role: inviteOrgRole,
                pod_id: podId,
                pod_role: invitePodRole,
                redirect_uri: inviteRedirectUri.trim() || defaultInviteRedirectUri,
            },
            {
                onSuccess: () => {
                    toast.success(`Invitation sent to ${email}`);
                    setAddDialogOpen(false);
                    setInviteEmail('');
                    setInviteOrgRole(OrganizationRole.ORG_MEMBER);
                    setInvitePodRole(PodRole.POD_USER);
                    setSelectedInviteRedirectUri(null);
                },
                onError: (err) => toast.error(getInviteErrorMessage(err)),
            }
        );
    };

    const handleRevokeInvitation = () => {
        if (!invitationPendingRevoke) return;
        if (!canInviteByEmail) {
            toast.error('Only organization owners and editors can manage email invites.');
            return;
        }

        setRevokingInvitationId(invitationPendingRevoke.id);
        revokeInvitation(invitationPendingRevoke.id, {
            onSuccess: () => {
                toast.success('Invitation revoked');
                setInvitationPendingRevoke(null);
            },
            onError: (err) => toast.error(getInviteErrorMessage(err)),
            onSettled: () => setRevokingInvitationId(null),
        });
    };

    const handleRemove = () => {
        if (!memberPendingRemove) return;
        if (!canManageMembers) return;
        removeMember(memberPendingRemove.id, {
            onSuccess: () => {
                toast.success('Member removed');
                setMemberPendingRemove(null);
            },
            onError: (err) => toast.error(`Failed to remove member: ${err.message}`),
        });
    };

    const handleMemberRoleAdd = (memberId: string, currentRoles: string[], roleName: string) => {
        if (!canManageMembers || currentRoles.includes(roleName)) return;
        updateRoles(
            { memberId, roles: [...currentRoles, roleName] },
            {
                onSuccess: () => toast.success('Roles updated'),
                onError: (err) => toast.error(`Failed to update roles: ${err.message}`),
            }
        );
    };

    const handleMemberRoleRemove = (memberId: string, currentRoles: string[], roleName: string) => {
        if (!canManageMembers || currentRoles.length <= 1) return;
        updateRoles(
            { memberId, roles: currentRoles.filter((role) => role !== roleName) },
            {
                onSuccess: () => toast.success('Roles updated'),
                onError: (err) => toast.error(`Failed to update roles: ${err.message}`),
            }
        );
    };

    const handleCreateRole = () => {
        if (!canManageRoles) return;
        const name = newRoleName.trim().toUpperCase().replace(/[^A-Z0-9_]+/g, '_');
        if (!name) {
            toast.error('Name the role first');
            return;
        }

        const templateRole = roles.find((role) => role.name === newRoleTemplate);

        createPodRole(
            {
                name,
                description: newRoleDescription.trim() || null,
                permission_ids: templateRole?.permission_ids?.length ? templateRole.permission_ids : ['pod.read'],
            },
            {
                onSuccess: () => {
                    toast.success('Role created');
                    setEditingRoleName(name);
                    setCreateRoleDialogOpen(false);
                    setNewRoleName('');
                    setNewRoleDescription('');
                    setNewRoleTemplate(PodRole.POD_VIEWER);
                },
                onError: (err) => toast.error(`Failed to create role: ${err.message}`),
            }
        );
    };

    const handleToggleRolePermission = (permissionId: string, checked: boolean) => {
        if (!canManageRoles) return;
        if (!editingRole || editingRole.is_system) return;
        const existing = new Set(editingRole.permission_ids || []);
        if (checked) existing.add(permissionId);
        else existing.delete(permissionId);

        updatePodRole(
            {
                roleName: editingRole.name,
                name: editingRole.name,
                description: editingRole.description,
                permission_ids: Array.from(existing).sort(),
            },
            {
                onSuccess: () => toast.success('Role permissions updated'),
                onError: (err) => toast.error(`Failed to update role: ${err.message}`),
            }
        );
    };

    const handleApproveJoinRequest = (requestId: string, userLabel: string) => {
        if (!canManageMembers) return;
        const approvalConfig = resolveApprovalConfig(requestId);
        setApprovingRequestId(requestId);

        approveJoinRequest(
            {
                joinRequestId: requestId,
                orgRole: approvalConfig.orgRole,
                podRole: approvalConfig.podRole,
                organizationId: pod?.organization_id,
            },
            {
                onSuccess: () => {
                    toast.success(`Access approved for ${userLabel}`);
                },
                onError: (err) => toast.error(`Failed to approve request: ${err.message}`),
                onSettled: () => setApprovingRequestId(null),
            }
        );
    };

    if (loadingMembers) {
        return (
            <div className="context-shell flex min-h-full items-center justify-center bg-transparent">
                <div className="surface-panel px-5 py-4">
                    <Loader2 className="h-5 w-5 animate-spin text-[var(--text-tertiary)]" />
                </div>
            </div>
        );
    }

    return (
        <PodSettingsShell
            podId={podId}
            title="Pod Settings"
            description="Manage who can get into this pod and what kind of access they have."
            action={canManageMembers ? (
                <Dialog open={addDialogOpen} onOpenChange={setAddDialogOpen}>
                    <DialogTrigger asChild>
                        <Button className="gap-2">
                            <Plus className="h-4 w-4" />
                            Add person
                        </Button>
                    </DialogTrigger>
                    <DialogContent className="sm:max-w-[520px] gap-3">
                        <DialogHeader className="pr-8">
                            <DialogTitle>Add access to pod</DialogTitle>
                            <DialogDescription>
                                Invite by email or add an existing organization member.
                            </DialogDescription>
                        </DialogHeader>
                        <Tabs defaultValue={canInviteByEmail ? 'email' : 'existing'} className="pt-2">
                            <TabsList className={`grid w-full ${canInviteByEmail ? 'grid-cols-2' : 'grid-cols-1'}`}>
                                {canInviteByEmail ? <TabsTrigger value="email">Invite by email</TabsTrigger> : null}
                                <TabsTrigger value="existing">Existing member</TabsTrigger>
                            </TabsList>
                            {canInviteByEmail ? (
                                <TabsContent value="email" className="mt-4 space-y-3">
                                    <div className="space-y-1.5">
                                        <label htmlFor="pod-invite-email" className="text-sm font-medium">Email</label>
                                        <Input
                                            id="pod-invite-email"
                                            type="email"
                                            autoComplete="email"
                                            placeholder="person@example.com"
                                            value={inviteEmail}
                                            onChange={(event) => setInviteEmail(event.target.value)}
                                        />
                                    </div>
                                    <div className="grid gap-3 sm:grid-cols-2">
                                        <div className="space-y-1.5">
                                            <label className="text-sm font-medium">Organization role</label>
                                            <Select value={inviteOrgRole} onValueChange={(value) => setInviteOrgRole(value as OrganizationRole)}>
                                                <SelectTrigger>
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value={OrganizationRole.ORG_OWNER}>Owner</SelectItem>
                                                    <SelectItem value={OrganizationRole.ORG_EDITOR}>Editor</SelectItem>
                                                    <SelectItem value={OrganizationRole.ORG_MEMBER}>Member</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                        <div className="space-y-1.5">
                                            <label className="text-sm font-medium">Pod role</label>
                                            <Select value={invitePodRole} onValueChange={(value) => setInvitePodRole(value as PodRole)}>
                                                <SelectTrigger>
                                                    <SelectValue />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    <SelectItem value={PodRole.POD_ADMIN}>Admin</SelectItem>
                                                    <SelectItem value={PodRole.POD_EDITOR}>Editor</SelectItem>
                                                    <SelectItem value={PodRole.POD_USER}>User</SelectItem>
                                                    <SelectItem value={PodRole.POD_VIEWER}>Viewer</SelectItem>
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    </div>
                                    <div className="space-y-1.5">
                                        <label htmlFor="pod-invite-redirect" className="text-sm font-medium">Redirect after accept</label>
                                        <Select
                                            value={inviteRedirectUri}
                                            onValueChange={setSelectedInviteRedirectUri}
                                        >
                                            <SelectTrigger id="pod-invite-redirect">
                                                <SelectValue />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {redirectOptions.map((option) => (
                                                    <SelectItem key={option.value} value={option.value}>
                                                        {option.label}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                        <p className="break-all text-xs text-[var(--text-tertiary)]">
                                            {inviteRedirectUri}
                                        </p>
                                    </div>
                                    <DialogFooter className="pt-1">
                                        <Button variant="ghost" onClick={() => setAddDialogOpen(false)}>Cancel</Button>
                                        <Button onClick={handleInviteByEmail} disabled={!inviteEmail.trim() || !pod?.organization_id || isInviting}>
                                            {isInviting ? 'Sending...' : 'Send invite'}
                                        </Button>
                                    </DialogFooter>
                                </TabsContent>
                            ) : null}
                            <TabsContent value="existing" className="mt-4 space-y-3">
                                {!canInviteByEmail ? (
                                    <div className="surface-panel-muted px-4 py-3 text-sm text-[var(--text-secondary)]">
                                        Only organization owners and editors can invite new people by email. You can still add existing organization members to this pod.
                                    </div>
                                ) : null}
                                <div className="space-y-1.5">
                                    <label className="text-sm font-medium">Member</label>
                                    <Select value={selectedOrgMemberId} onValueChange={setSelectedOrgMemberId}>
                                        <SelectTrigger>
                                            <SelectValue placeholder="Select a member" />
                                        </SelectTrigger>
                                        <SelectContent>
                                            {availableMembers.map((member) => (
                                                <SelectItem key={member.id} value={member.id}>
                                                    {member.user?.full_name || member.user?.email || 'Unknown User'}
                                                </SelectItem>
                                            ))}
                                            {availableMembers.length === 0 ? (
                                                <div className="p-2 text-center text-sm text-[var(--text-tertiary)]">No more members to add</div>
                                            ) : null}
                                        </SelectContent>
                                    </Select>
                                </div>
                                <div className="space-y-1.5">
                                    <label className="text-sm font-medium">Role</label>
                                    <Select value={selectedRole} onValueChange={(value) => setSelectedRole(value as PodRole)}>
                                        <SelectTrigger>
                                            <SelectValue />
                                        </SelectTrigger>
                                        <SelectContent>
                                            <SelectItem value={PodRole.POD_ADMIN}>Admin</SelectItem>
                                            <SelectItem value={PodRole.POD_EDITOR}>Editor</SelectItem>
                                            <SelectItem value={PodRole.POD_USER}>User</SelectItem>
                                            <SelectItem value={PodRole.POD_VIEWER}>Viewer</SelectItem>
                                        </SelectContent>
                                    </Select>
                                </div>
                                <DialogFooter className="pt-1">
                                    <Button variant="ghost" onClick={() => setAddDialogOpen(false)}>Cancel</Button>
                                    <Button onClick={handleAddMember} disabled={!selectedOrgMemberId || isAdding}>
                                        {isAdding ? 'Adding...' : 'Add member'}
                                    </Button>
                                </DialogFooter>
                            </TabsContent>
                        </Tabs>
                    </DialogContent>
                </Dialog>
            ) : null}
        >
            <div className="w-full max-w-5xl space-y-5">
                <ResourceMetricStrip className="lemma-index-tabs-left">
                    <ResourceMetricButton active={activeView === 'people'} label="People" count={members.length} onClick={() => setActiveView('people')} />
                    <ResourceMetricButton active={activeView === 'invites'} label="Email invites" count={pendingPodInvitations.length} onClick={() => setActiveView('invites')} />
                    <ResourceMetricButton active={activeView === 'requests'} label="Access requests" count={pendingJoinRequests.length} onClick={() => setActiveView('requests')} />
                    <ResourceMetricButton active={activeView === 'roles'} label="Roles" count={roles.length || ROLE_GUIDANCE.length} onClick={() => setActiveView('roles')} />
                    {canManageMembers ? (
                        <ResourceMetricButton active={activeView === 'available'} label="Available" count={availableMembers.length} onClick={() => setActiveView('available')} />
                    ) : null}
                </ResourceMetricStrip>

                {activeView === 'people' ? (
                    members.length === 0 ? (
                        <EmptyState
                            variant="panel"
                            icon={<Users className="h-5 w-5" />}
                            title="No people yet"
                            description="Start with the people who need to operate this pod day to day, then widen access once the work settles."
                            action={canManageMembers ? (
                                <Button size="sm" className="gap-2" onClick={() => setAddDialogOpen(true)}>
                                    <Plus className="h-4 w-4" />
                                    Add person
                                </Button>
                            ) : null}
                        />
                    ) : (
                        <div className="lemma-index-list">
                            {members.map((member) => {
                                const orgMember = orgMembers.find((item) => item.user_id === member.user_id);
                                const user = orgMember?.user;
                                const podMemberId = member.pod_member_id;
                                const memberRoles = resolveMemberRoles(member);
                                const addableRoles = roleNames.filter((roleName) => !memberRoles.includes(roleName));
                                const displayName = user?.first_name
                                    ? `${user.first_name} ${user.last_name || ''}`.trim()
                                    : (member.user_name || user?.email || member.user_email || 'Unknown User');

                                return (
                                    <div key={podMemberId} className="lemma-index-row group flex items-center gap-2">
                                        <Avatar className="h-8 w-8 shrink-0 border border-[color:var(--border-subtle)]">
                                            <AvatarImage src={user?.avatar_url} />
                                            <AvatarFallback>
                                                {(user?.first_name?.[0] || user?.email?.[0] || 'U').toUpperCase()}
                                            </AvatarFallback>
                                        </Avatar>
                                        <div className="flex min-w-0 flex-1 items-baseline gap-2">
                                            <p className="truncate text-sm font-medium text-[var(--text-primary)]">{displayName}</p>
                                            <p className="hidden truncate text-xs text-[var(--text-secondary)] md:block">
                                                {user?.email || member.user_email || 'No email on file'}
                                            </p>
                                        </div>
                                        <div className="flex max-w-[22rem] shrink-0 flex-wrap justify-end gap-1">
                                            {memberRoles.map((roleName) => (
                                                <button
                                                    key={roleName}
                                                    type="button"
                                                    disabled={!canManageMembers || memberRoles.length <= 1 || isUpdatingRoles}
                                                    onClick={() => handleMemberRoleRemove(podMemberId, memberRoles, roleName)}
                                                    className="chip chip-sm chip-muted shrink-0 gap-1.5 disabled:cursor-default"
                                                    title={canManageMembers && memberRoles.length > 1 ? 'Remove role' : undefined}
                                                >
                                                    <span>{formatRoleLabel(roleName)}</span>
                                                    <span className="text-xs font-medium uppercase text-[var(--text-tertiary)]">
                                                        {getRoleTypeLabel(roleName, roleMetaByName)}
                                                    </span>
                                                </button>
                                            ))}
                                        </div>
                                        {canManageMembers ? (
                                            <div className="flex shrink-0 items-center gap-1">
                                                <Select
                                                    onValueChange={(value) => handleMemberRoleAdd(podMemberId, memberRoles, value)}
                                                    disabled={isUpdatingRoles || addableRoles.length === 0}
                                                >
                                                    <SelectTrigger className="h-8 w-[120px]">
                                                        <SelectValue placeholder="Add role" />
                                                    </SelectTrigger>
                                                    <SelectContent>
                                                        {addableRoles.map((roleName) => (
                                                            <SelectItem key={roleName} value={roleName}>
                                                                {formatRoleLabel(roleName)}
                                                            </SelectItem>
                                                        ))}
                                                    </SelectContent>
                                                </Select>
                                                <ResourceActionsMenu ariaLabel={`Open actions for ${displayName}`} triggerClassName="h-8 w-8">
                                                    <DestructiveResourceActionItem
                                                        disabled={isRemoving}
                                                        onSelect={() => setMemberPendingRemove({ id: podMemberId, label: displayName })}
                                                    >
                                                        Remove from pod
                                                    </DestructiveResourceActionItem>
                                                </ResourceActionsMenu>
                                            </div>
                                        ) : null}
                                    </div>
                                );
                            })}
                        </div>
                    )
                ) : null}

                {activeView === 'invites' ? (
                    loadingInvitations ? (
                        <QuietEmptyState icon={<Loader2 className="h-4 w-4 animate-spin" />}>Loading invites...</QuietEmptyState>
                    ) : pendingPodInvitations.length === 0 ? (
                        <EmptyState
                            variant="compact"
                            icon={<Mail className="h-4 w-4" />}
                            title="No pending email invites"
                            description="Email invitations that grant pod access will appear here."
                            action={canInviteByEmail ? (
                                <Button variant="outline" size="sm" className="gap-2" onClick={() => setAddDialogOpen(true)}>
                                    <Mail className="h-3.5 w-3.5" />
                                    Invite
                                </Button>
                            ) : null}
                        />
                    ) : (
                        <div className="lemma-index-list">
                            {pendingPodInvitations.map((invite) => {
                                const isRevokingThis = isRevokingInvitation && revokingInvitationId === invite.id;
                                return (
                                    <div key={invite.id} className="lemma-index-row group flex items-center gap-2">
                                        <Mail className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                                        <div className="min-w-0 flex-1">
                                            <p className="truncate text-sm font-medium text-[var(--text-primary)]">{invite.email}</p>
                                            <p className="mt-0.5 truncate text-xs text-[var(--text-secondary)]">
                                                Pod {formatRoleLabel((invite.pod_role || PodRole.POD_USER) as PodRole)} · Org {formatOrgRoleLabel(invite.role)}
                                            </p>
                                        </div>
                                        <span className="hidden shrink-0 text-xs text-[var(--text-tertiary)] md:inline">
                                            Expires {new Date(invite.expires_at).toLocaleDateString()}
                                        </span>
                                        {canInviteByEmail ? (
                                            <ResourceActionsMenu ariaLabel={`Open actions for invite ${invite.email}`} triggerClassName="h-8 w-8">
                                                <DestructiveResourceActionItem
                                                    disabled={isRevokingThis}
                                                    onSelect={() => setInvitationPendingRevoke({ id: invite.id, email: invite.email })}
                                                >
                                                    Revoke invite
                                                </DestructiveResourceActionItem>
                                            </ResourceActionsMenu>
                                        ) : null}
                                    </div>
                                );
                            })}
                        </div>
                    )
                ) : null}

                {activeView === 'requests' ? (
                    <>
                        <div className="mb-2 flex justify-end">
                            <Button variant="ghost" size="sm" className="h-7 px-2 text-xs" onClick={() => void refetchJoinRequests()}>
                                Refresh
                            </Button>
                        </div>
                        {loadingJoinRequests ? (
                            <QuietEmptyState icon={<Loader2 className="h-4 w-4 animate-spin" />}>Loading requests...</QuietEmptyState>
                        ) : isJoinRequestsError ? (
                            <div className="state-surface-error rounded-lg px-3 py-3 text-sm text-[var(--text-secondary)]">
                                {joinRequestsError instanceof Error
                                    ? joinRequestsError.message
                                    : 'Unable to load pending access requests.'}
                            </div>
                        ) : pendingJoinRequests.length === 0 ? (
                            <QuietEmptyState icon={<CheckCircle2 className="h-4 w-4" />}>No pending pod access requests.</QuietEmptyState>
                        ) : (
                            <div className="lemma-index-list">
                                {pendingJoinRequests.map((request) => {
                                    const requesterOrgMember = orgMembers.find((member) => member.user_id === request.user_id);
                                    const requester = requesterOrgMember?.user;
                                    const requesterLabel = requester?.full_name || requester?.email || request.user_id;
                                    const approvalConfig = resolveApprovalConfig(request.id);
                                    const isApprovingThis = isApprovingJoinRequest && approvingRequestId === request.id;

                                    return (
                                        <div key={request.id} className="lemma-index-row group flex flex-col gap-3 py-3">
                                            <div className="flex min-w-0 items-center gap-2">
                                                <UserPlus className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                                                <div className="flex min-w-0 flex-1 items-baseline gap-2">
                                                    <p className="truncate text-sm font-medium text-[var(--text-primary)]">{requesterLabel}</p>
                                                    <p className="hidden truncate text-xs text-[var(--text-secondary)] md:block">
                                                        Requested {new Date(request.requested_at).toLocaleString()}
                                                    </p>
                                                </div>
                                            </div>

                                            {canManageMembers ? (
                                                <div className="grid gap-2 pl-5 sm:grid-cols-[minmax(0,1fr)_minmax(0,1fr)_auto] sm:items-end">
                                                    <div className="space-y-1.5">
                                                        <p className="type-eyebrow-medium">Organization role</p>
                                                        <Select
                                                            value={approvalConfig.orgRole}
                                                            onValueChange={(value) => setApprovalConfig(request.id, { orgRole: value as OrganizationRole })}
                                                        >
                                                            <SelectTrigger>
                                                                <SelectValue />
                                                            </SelectTrigger>
                                                            <SelectContent>
                                                                <SelectItem value={OrganizationRole.ORG_OWNER}>Owner</SelectItem>
                                                                <SelectItem value={OrganizationRole.ORG_EDITOR}>Editor</SelectItem>
                                                                <SelectItem value={OrganizationRole.ORG_MEMBER}>Member</SelectItem>
                                                            </SelectContent>
                                                        </Select>
                                                    </div>
                                                    <div className="space-y-1.5">
                                                        <p className="type-eyebrow-medium">Pod role</p>
                                                        <Select
                                                            value={approvalConfig.podRole}
                                                            onValueChange={(value) => setApprovalConfig(request.id, { podRole: value as PodRole })}
                                                        >
                                                            <SelectTrigger>
                                                                <SelectValue />
                                                            </SelectTrigger>
                                                            <SelectContent>
                                                                <SelectItem value={PodRole.POD_ADMIN}>Admin</SelectItem>
                                                                <SelectItem value={PodRole.POD_EDITOR}>Editor</SelectItem>
                                                                <SelectItem value={PodRole.POD_USER}>User</SelectItem>
                                                                <SelectItem value={PodRole.POD_VIEWER}>Viewer</SelectItem>
                                                            </SelectContent>
                                                        </Select>
                                                    </div>
                                                    <Button
                                                        size="sm"
                                                        onClick={() => handleApproveJoinRequest(request.id, requesterLabel)}
                                                        disabled={isApprovingThis}
                                                    >
                                                        {isApprovingThis ? 'Approving...' : 'Approve'}
                                                    </Button>
                                                </div>
                                            ) : null}
                                        </div>
                                    );
                                })}
                            </div>
                        )}
                    </>
                ) : null}

                {activeView === 'roles' ? (
                    <div className="space-y-4">
                        <section className="space-y-3">
                            <div className="flex flex-wrap items-center justify-between gap-3">
                                <div>
                                    <h2 className="text-sm font-medium text-[var(--text-primary)]">Roles</h2>
                                    <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                                        Reusable access profiles for pod members.
                                    </p>
                                </div>
                                {canManageRoles ? (
                                    <Button size="sm" variant="outline" className="gap-2" onClick={() => setCreateRoleDialogOpen(true)}>
                                        <Plus className="h-3.5 w-3.5" />
                                        New custom role
                                    </Button>
                                ) : (
                                    <ShieldCheck className="h-4 w-4 text-[var(--delight)]" />
                                )}
                            </div>

                            <div className="lemma-index-list">
                                {(roles.length ? roles : ROLE_GUIDANCE.map((role) => ({
                                    name: `POD_${role.label.toUpperCase()}`,
                                    description: role.description,
                                    is_system: true,
                                    permission_ids: [],
                                }))).map((role) => {
                                    const isActive = editingRole?.name === role.name;
                                    return (
                                        <Button
                                            key={role.name}
                                            variant="ghost"
                                            onClick={() => setEditingRoleName(role.name)}
                                            className="lemma-index-row group flex h-auto w-full items-center justify-start gap-3 text-left"
                                            data-active={isActive}
                                        >
                                            <div className="w-36 shrink-0">
                                                <p className="truncate text-sm font-medium text-[var(--text-primary)]">{formatRoleLabel(role.name)}</p>
                                                <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">{role.is_system ? 'Preset' : 'Custom'}</p>
                                            </div>
                                            <p className="min-w-0 flex-1 truncate text-sm text-[var(--text-secondary)]">
                                                {getRoleDescription(role.name, role.description)}
                                            </p>
                                            <p className="hidden max-w-sm shrink-0 truncate text-xs text-[var(--text-tertiary)] lg:block">
                                                {getRoleCapabilitySummary(role)}
                                            </p>
                                            {!role.is_system ? (
                                                <span className="shrink-0 text-xs font-medium text-[var(--text-secondary)]">
                                                    Edit
                                                </span>
                                            ) : null}
                                        </Button>
                                    );
                                })}
                            </div>
                        </section>

                        {editingRole && !editingRole.is_system ? (
                        <section className="min-w-0 space-y-4 border-t border-[var(--border-subtle)] pt-4">
                            <>
                                <div className="flex flex-wrap items-start justify-between gap-3">
                                    <div className="min-w-0">
                                        <h2 className="text-base font-medium text-[var(--text-primary)]">{formatRoleLabel(editingRole.name)}</h2>
                                        <p className="mt-1 max-w-3xl text-sm leading-6 text-[var(--text-secondary)]">
                                            {getRoleDescription(editingRole.name, editingRole.description)}
                                        </p>
                                        <p className="mt-1 text-xs text-[var(--text-tertiary)]">
                                            {getRoleCapabilitySummary(editingRole)}
                                        </p>
                                    </div>
                                    {canManageRoles ? (
                                        <Button
                                            variant="outline"
                                            size="sm"
                                            disabled={isDeletingRole}
                                            onClick={() => deletePodRole(editingRole.name, {
                                                onSuccess: () => {
                                                    toast.success('Role deleted');
                                                    setEditingRoleName(null);
                                                },
                                                onError: (err) => toast.error(`Failed to delete role: ${err.message}`),
                                            })}
                                        >
                                            Delete role
                                        </Button>
                                    ) : null}
                                </div>

                                <div className="space-y-3">
                                    <div className="space-y-3">
                                        <div className="flex items-start justify-between gap-3">
                                            <div>
                                                <h3 className="text-sm font-medium text-[var(--text-primary)]">Capabilities</h3>
                                                <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                                                    Choose what this role can do across the pod. Share specific resources from the resource itself.
                                                </p>
                                            </div>
                                            {isSavingRole ? <Loader2 className="h-4 w-4 animate-spin text-[var(--text-tertiary)]" /> : null}
                                        </div>

                                        <div className="grid gap-x-6 gap-y-4 lg:grid-cols-2">
                                            {groupedPermissions.map((group) => (
                                                <div key={group.name} className="border-b border-[var(--border-subtle)] pb-3">
                                                    <div className="mb-2 flex items-center justify-between gap-2">
                                                        <h4 className="text-xs font-semibold uppercase tracking-wide text-[var(--text-secondary)]">{group.label}</h4>
                                                        <span className="text-xs text-[var(--text-tertiary)]">
                                                            {group.permissions.filter((permission) => editingRole.permission_ids?.includes(permission.id)).length} selected
                                                        </span>
                                                    </div>
                                                    <div className="space-y-1">
                                                        {group.permissions.map((permission) => {
                                                            const checked = editingRole.permission_ids?.includes(permission.id) || false;
                                                            return (
                                                                <label key={permission.id} className="flex items-start gap-2 rounded-md py-1.5">
                                                                    <Checkbox
                                                                        checked={checked}
                                                                        disabled={!canManageRoles || isSavingRole}
                                                                        onCheckedChange={(value) => handleToggleRolePermission(permission.id, value === true)}
                                                                    />
                                                                    <span className="min-w-0 flex-1">
                                                                        <span className="block truncate text-sm font-medium text-[var(--text-primary)]">{shortPermissionLabel(permission.id)}</span>
                                                                        <span className="mt-0.5 block text-xs leading-5 text-[var(--text-secondary)]">{permission.description}</span>
                                                                    </span>
                                                                </label>
                                                            );
                                                        })}
                                                    </div>
                                                </div>
                                            ))}
                                            {groupedPermissions.length === 0 ? (
                                                <QuietEmptyState>No permissions are available in the catalog.</QuietEmptyState>
                                            ) : null}
                                        </div>
                                    </div>
                                </div>
                            </>
                        </section>
                        ) : null}
                    </div>
                ) : null}

                <Dialog open={createRoleDialogOpen} onOpenChange={setCreateRoleDialogOpen}>
                    <DialogContent className="sm:max-w-lg">
                        <DialogHeader>
                            <DialogTitle>Create custom role</DialogTitle>
                            <DialogDescription>
                                Define a pod-scoped role for a specific access pattern.
                            </DialogDescription>
                        </DialogHeader>
                        <div className="space-y-4 py-2">
                            <div className="space-y-1.5">
                                <label htmlFor="custom-role-name" className="text-sm font-medium">Role name</label>
                                <Input
                                    id="custom-role-name"
                                    value={newRoleName}
                                    onChange={(event) => setNewRoleName(event.target.value)}
                                    placeholder="FINANCE_REVIEWERS"
                                />
                            </div>
                            <div className="space-y-1.5">
                                <label htmlFor="custom-role-description" className="text-sm font-medium">Description</label>
                                <Input
                                    id="custom-role-description"
                                    value={newRoleDescription}
                                    onChange={(event) => setNewRoleDescription(event.target.value)}
                                    placeholder="Reviews finance tables and folders"
                                />
                            </div>
                            <div className="space-y-1.5">
                                <label className="text-sm font-medium">Start from</label>
                                <Select value={newRoleTemplate} onValueChange={setNewRoleTemplate}>
                                    <SelectTrigger>
                                        <SelectValue />
                                    </SelectTrigger>
                                    <SelectContent>
                                        {roles.filter((role) => role.is_system).map((role) => (
                                            <SelectItem key={role.name} value={role.name}>
                                                {formatRoleLabel(role.name)}
                                            </SelectItem>
                                        ))}
                                    </SelectContent>
                                </Select>
                                <p className="text-xs leading-5 text-[var(--text-secondary)]">
                                    {getRoleDescription(newRoleTemplate, roles.find((role) => role.name === newRoleTemplate)?.description)}
                                </p>
                                <p className="text-xs text-[var(--text-tertiary)]">
                                    Starts with {getRoleCapabilitySummary(roles.find((role) => role.name === newRoleTemplate) || { name: newRoleTemplate })}.
                                </p>
                            </div>
                        </div>
                        <DialogFooter>
                            <Button variant="ghost" onClick={() => setCreateRoleDialogOpen(false)}>Cancel</Button>
                            <Button onClick={handleCreateRole} disabled={!canManageRoles || isCreatingRole || !newRoleName.trim()}>
                                {isCreatingRole ? 'Creating...' : 'Create role'}
                            </Button>
                        </DialogFooter>
                    </DialogContent>
                </Dialog>

                {activeView === 'available' ? (
                    availableMembers.length === 0 ? (
                        <QuietEmptyState icon={<CheckCircle2 className="h-4 w-4" />}>Everyone in the organization who needs access is already here.</QuietEmptyState>
                    ) : (
                        <div className="lemma-index-list">
                            {availableMembers.map((member) => (
                                <div key={member.id} className="lemma-index-row group flex items-center gap-2">
                                    <Users className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                                    <div className="flex min-w-0 flex-1 items-baseline gap-2">
                                        <p className="truncate text-sm font-medium text-[var(--text-primary)]">
                                            {member.user?.full_name || member.user?.email || 'Unknown user'}
                                        </p>
                                        <p className="hidden truncate text-xs text-[var(--text-secondary)] md:block">{member.user?.email}</p>
                                    </div>
                                    <Button variant="ghost" size="sm" className="h-7 gap-1.5 px-2 text-xs" onClick={() => setAddDialogOpen(true)}>
                                        Add people
                                        <ArrowRight className="h-3.5 w-3.5" />
                                    </Button>
                                </div>
                            ))}
                        </div>
                    )
                ) : null}
            </div>
            <DestructiveConfirmationDialog
                open={Boolean(memberPendingRemove)}
                onOpenChange={(open) => {
                    if (!open) setMemberPendingRemove(null);
                }}
                title="Remove pod member"
                description={`Remove ${memberPendingRemove?.label ?? 'this member'} from ${podName}?`}
                resourceName={memberPendingRemove?.label ?? 'member'}
                confirmationText=""
                consequences={[
                    'They will lose access to this pod.',
                    'Their organization membership is not removed.',
                ]}
                confirmLabel="Remove member"
                pendingLabel="Removing member..."
                isPending={isRemoving}
                onConfirm={handleRemove}
            />
            <DestructiveConfirmationDialog
                open={Boolean(invitationPendingRevoke)}
                onOpenChange={(open) => {
                    if (!open) setInvitationPendingRevoke(null);
                }}
                title="Revoke invite"
                description={`Revoke the pending invite for ${invitationPendingRevoke?.email ?? 'this person'}?`}
                resourceName={invitationPendingRevoke?.email ?? 'invite'}
                confirmationText=""
                consequences={[
                    'The invite link will stop working.',
                    'You can send a new invite later if needed.',
                ]}
                confirmLabel="Revoke invite"
                pendingLabel="Revoking invite..."
                isPending={isRevokingInvitation}
                onConfirm={handleRevokeInvitation}
            />
        </PodSettingsShell>
    );
}

const ROLE_GUIDANCE = [
    {
        label: 'Admin',
        description: 'Owns the pod setup and can change who else is invited.',
    },
    {
        label: 'Editor',
        description: 'Can actively update processes, content, and operating setup without managing membership.',
    },
    {
        label: 'User',
        description: 'Can use the pod day to day and contribute without reconfiguring the structure.',
    },
    {
        label: 'Viewer',
        description: 'Read-only access for stakeholders who need visibility without operational control.',
    },
];

type PermissionCatalogItem = {
    id: string;
    resource_type?: string | null;
    description?: string | null;
};

function groupPermissionCatalog(permissions: PermissionCatalogItem[]) {
    const groups = new Map<string, PermissionCatalogItem[]>();

    permissions.forEach((permission) => {
        const [area] = permission.id.split('.');
        const groupName = area || 'other';
        groups.set(groupName, [...(groups.get(groupName) || []), permission]);
    });

    return Array.from(groups.entries()).map(([name, groupPermissions]) => ({
        name,
        label: formatAccessName(name),
        permissions: groupPermissions.sort((first, second) => first.id.localeCompare(second.id)),
    }));
}

function getRoleDescription(roleName: string, fallback?: string | null) {
    const guidance = ROLE_GUIDANCE.find((role) => roleName === `POD_${role.label.toUpperCase()}`);
    if (guidance) return guidance.description;

    if (fallback) return fallback;

    return 'Custom pod role.';
}

type RoleMeta = {
    name: string;
    is_system?: boolean;
};

function resolveMemberRoles(member: { roles?: string[] | null; role?: string | null }) {
    if (member.roles?.length) return member.roles;
    if (member.role) return [member.role];
    return [PodRole.POD_USER];
}

function getRoleTypeLabel(roleName: string, roleMetaByName: Map<string, RoleMeta>) {
    const role = roleMetaByName.get(roleName);
    if (role) return role.is_system ? 'Preset' : 'Custom';
    return isSystemRoleName(roleName) ? 'Preset' : 'Custom';
}

function isSystemRoleName(roleName: string) {
    return roleName === PodRole.POD_ADMIN ||
        roleName === PodRole.POD_EDITOR ||
        roleName === PodRole.POD_USER ||
        roleName === PodRole.POD_VIEWER;
}

function getRoleCapabilitySummary(role: { name: string; permission_ids?: string[]; description?: string | null; is_system?: boolean }) {
    const permissions = new Set(role.permission_ids || []);

    if (role.name === PodRole.POD_ADMIN || permissions.has('pod.member.manage') || permissions.has('pod.role.manage')) {
        return 'Full pod administration, membership, roles, and deletes.';
    }

    if (role.name === PodRole.POD_EDITOR || permissions.has('datastore.table.create') || permissions.has('agent.create') || permissions.has('function.create')) {
        return 'Can create and update pod content, agents, functions, workflows, and files.';
    }

    if (role.name === PodRole.POD_USER || permissions.has('agent.execute') || permissions.has('function.execute') || permissions.has('datastore.record.write')) {
        return 'Can use the pod day to day, run agents/functions, and contribute records or files.';
    }

    if (role.name === PodRole.POD_VIEWER || permissions.has('pod.read')) {
        return 'Can view pod content without changing setup or resources.';
    }

    if (role.description) return role.description;

    return 'Custom access profile. Open to review capabilities.';
}

function shortPermissionLabel(permissionId: string) {
    const parts = permissionId.split('.');
    if (parts.length <= 1) return permissionId;

    return parts.slice(1).join(' ');
}

function formatAccessName(value: string | null | undefined) {
    const normalized = String(value || 'resource').replace(/^POD_/, '').replace(/^ORG_/, '').replace(/_/g, ' ').toLowerCase();
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function formatRoleLabel(role: string) {
    const normalized = String(role).replace('POD_', '').toLowerCase().replace(/_/g, ' ');
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function formatOrgRoleLabel(role: OrganizationRole) {
    const normalized = String(role).replace('ORG_', '').toLowerCase().replace(/_/g, ' ');
    return normalized.charAt(0).toUpperCase() + normalized.slice(1);
}

function getInviteErrorMessage(error: Error) {
    const details = error as Error & { code?: string };

    if (details.code === 'IDENTITY_ACCESS_DENIED') {
        return 'Only organization owners and editors can invite members.';
    }

    return `Failed to send invite: ${error.message}`;
}
