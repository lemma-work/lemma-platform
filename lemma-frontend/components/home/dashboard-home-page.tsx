'use client';

import { useMemo } from 'react';
import Link from 'next/link';
import { ArrowRight, Mail } from '@/components/ui/icons';
import { useOrganization } from '@/components/dashboard/org-context';
import { HomeTopbar } from '@/components/home/home-topbar';
import { HomeWorkspaceOverview } from '@/components/home/home-workspace-overview';
import { useMyOrganizationInvitations, useOrganizationMembers } from '@/lib/hooks/use-organizations';
import { useProfile } from '@/lib/hooks/use-user';
import { useAccessiblePods } from '@/lib/hooks/use-pods';
import { OrganizationInvitationStatus, OrganizationRole } from '@/lib/types';
import { Button } from '@/components/ui/button';
import { formatRoleLabel } from '@/lib/utils/role-labels';

export default function DashboardHomePage() {
    const { currentOrg } = useOrganization();
    const { data: profile } = useProfile();
    const { data: membersResponse } = useOrganizationMembers(currentOrg?.id || '');
    const {
        data: podsResponse,
        isLoading: isLoadingPods,
        isError: isPodsError,
        error: podsError,
    } = useAccessiblePods();
    const pods = podsResponse?.items || [];

    const orgRole = useMemo(() => {
        const members = membersResponse?.items || [];
        return members.find((member) => member.user_id === profile?.id)?.role;
    }, [membersResponse?.items, profile?.id]);

    const isBuilderHome =
        orgRole === OrganizationRole.ORG_OWNER ||
        orgRole === OrganizationRole.ORG_EDITOR;

    return (
        <div className="min-h-screen bg-[var(--pod-shell-bg)] text-[var(--text-primary)]">
            <HomeTopbar />
            <main className="min-h-[calc(100vh-4rem)] bg-[var(--pod-main-bg)] px-4 py-4 sm:px-7 sm:py-6 lg:px-12">
                <div className="mx-auto flex w-full max-w-6xl flex-col pb-16 sm:pb-20">
                    <PendingInvitationsHomePanel />
                    <HomeWorkspaceOverview
                        pods={pods}
                        showOrganizationName={podsResponse?.hasMultipleOrganizations}
                        showCreateAction={isBuilderHome}
                        isLoading={isLoadingPods}
                        error={isPodsError ? podsError : null}
                    />
                </div>
            </main>
        </div>
    );
}

function PendingInvitationsHomePanel() {
    const { data, isLoading } = useMyOrganizationInvitations(OrganizationInvitationStatus.PENDING);
    const invitations = data?.items || [];
    const firstInvitation = invitations[0];

    if (isLoading || invitations.length === 0 || !firstInvitation) return null;

    return (
        <section className="lemma-pop-card mx-auto mb-8 max-w-4xl p-4 sm:p-5">
            <div className="flex flex-col gap-4 sm:flex-row sm:items-center sm:justify-between">
                <div className="flex min-w-0 items-start gap-3">
                    <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg border border-[var(--row-border)] bg-[var(--delight-soft)] text-[var(--delight)]">
                        <Mail className="h-5 w-5" />
                    </div>
                    <div className="min-w-0">
                        <p className="text-sm font-semibold text-[var(--text-primary)]">
                            {invitations.length === 1
                                ? firstInvitation.pod_name
                                    ? `Invitation to ${firstInvitation.pod_name}`
                                    : 'You have a workspace invitation'
                                : `${invitations.length} workspace invitations`}
                        </p>
                        <p className="mt-1 line-clamp-2 text-sm leading-6 text-[var(--text-secondary)]">
                            {firstInvitation.pod_description ||
                                `Role: ${formatRoleLabel(firstInvitation.pod_role || firstInvitation.role)}`}
                        </p>
                    </div>
                </div>
                <Button asChild className="shrink-0 gap-2">
                    <Link href={`/invitations/${firstInvitation.id}/accept`}>
                        Review invite
                        <ArrowRight className="h-4 w-4" />
                    </Link>
                </Button>
            </div>
        </section>
    );
}
