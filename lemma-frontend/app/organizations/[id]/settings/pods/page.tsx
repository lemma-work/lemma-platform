'use client';

import { use } from 'react';
import { ProtectedRoute } from '@/components/auth/protected-route';
import { PlainPageShell } from '@/components/dashboard/plain-page-shell';
import { ProductIcon } from '@/components/pod/product-icon';
import { HomeWorkspaceOverview } from '@/components/home/home-workspace-overview';
import { SettingsHelpText, SettingsStack } from '@/components/settings/settings-kit';
import { SettingsPageHeading } from '@/components/settings/settings-page-heading';
import { useOrganizationDetails } from '@/lib/hooks/use-organizations';
import { useAccessiblePods } from '@/lib/hooks/use-pods';
import { OrganizationRole } from '@/lib/types';

/**
 * Every pod in this organization.
 *
 * A page rather than a sidebar list: an organization with a dozen pods pushed
 * the rest of the rail out of view, and the list is worth room for a
 * description and a last-touched date once it has one.
 */
function OrganizationPods({ organizationId }: { organizationId: string }) {
    const { data: organization } = useOrganizationDetails(organizationId);
    const navigation = useAccessiblePods();
    // Kept as the group rather than as its pods, because "no pods" and "no such
    // organization" are different answers and `?? []` collapsed them into the
    // first -- an id the viewer cannot reach rendered the empty state, complete
    // with an enabled "New pod".
    const group = navigation.data.groups.find(
        (candidate) => candidate.organization.id === organizationId,
    );
    const missing = !navigation.isLoading && !navigation.error && !group;
    // Creating a pod is an organization-level capability; whether each listed
    // pod can be shared or deleted is decided per pod, from its own role.
    const canCreatePod =
        group?.organization.role === OrganizationRole.ORG_OWNER ||
        group?.organization.role === OrganizationRole.ORG_EDITOR;

    return (
        <PlainPageShell
            title="Pods"
            icon={<ProductIcon kind="settings" size="sm" />}
            backHref="/home"
            backLabel="Home"
            meta={organization?.name || 'Organization'}
            contentWidthClassName="max-w-6xl"
            contentAlign="left"
            contentClassName="pb-16 sm:pb-20"
        >
            <SettingsStack className="office-arrive">
                <SettingsPageHeading
                    title="Pods"
                    description="Every pod this organization owns."
                />

                {/* The same list home puts pods in -- search, app shortcuts,
                    share and delete included -- rather than a second, poorer
                    presentation of the same objects. */}
                {missing ? (
                    <SettingsHelpText>
                        This organization could not be found, or you no longer
                        have access to it.
                    </SettingsHelpText>
                ) : (
                    <HomeWorkspaceOverview
                        pods={group?.pods ?? []}
                        isLoading={navigation.isLoading}
                        error={navigation.error}
                        showCreateAction={canCreatePod}
                    />
                )}
            </SettingsStack>
        </PlainPageShell>
    );
}

export default function OrganizationPodsPage({
    params,
}: {
    params: Promise<{ id: string }>;
}) {
    const { id } = use(params);
    return (
        <ProtectedRoute>
            <OrganizationPods organizationId={id} />
        </ProtectedRoute>
    );
}
