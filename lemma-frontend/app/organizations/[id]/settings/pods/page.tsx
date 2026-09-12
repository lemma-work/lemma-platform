'use client';

import { use } from 'react';
import { ProtectedRoute } from '@/components/auth/protected-route';
import { PlainPageShell } from '@/components/dashboard/plain-page-shell';
import { ProductIcon } from '@/components/pod/product-icon';
import { HomeWorkspaceOverview } from '@/components/home/home-workspace-overview';
import { SettingsStack } from '@/components/settings/settings-kit';
import { SettingsPageHeading } from '@/components/settings/settings-page-heading';
import { useOrganizationDetails } from '@/lib/hooks/use-organizations';
import { useAccessiblePods } from '@/lib/hooks/use-pods';

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
    const pods =
        navigation.data.groups.find((group) => group.organization.id === organizationId)
            ?.pods ?? [];

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
                <HomeWorkspaceOverview
                    pods={pods}
                    isLoading={navigation.isLoading}
                    error={navigation.error}
                    showCreateAction
                />
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
