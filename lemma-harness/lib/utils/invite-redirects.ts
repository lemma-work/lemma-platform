import { config } from '@/lib/config';
import { PodRole, type OrganizationRole } from '@/lib/types';

interface InviteApp {
    name: string;
    url: string;
    description?: string | null;
}

export interface InviteRedirectOption {
    label: string;
    value: string;
    description?: string | null;
}

export function getSiteOrigin(): string {
    const configured = config.SITE_URL || '';

    try {
        return new URL(configured).origin;
    } catch {
        if (typeof window !== 'undefined') {
            return window.location.origin;
        }

        return 'https://localhost';
    }
}

export function toAbsoluteSiteUrl(path: string): string {
    const value = path.trim();

    if (/^https?:\/\//i.test(value)) {
        return value;
    }

    const normalizedPath = value.startsWith('/') ? value : `/${value}`;
    return `${getSiteOrigin()}${normalizedPath}`;
}

export function getPodInviteRedirectOptions({
    podId,
    apps,
}: {
    podId: string;
    apps: InviteApp[];
}): InviteRedirectOption[] {
    const podUrl = toAbsoluteSiteUrl(`/pod/${podId}`);
    const appOptions = apps.map((app) => ({
        label: app.name,
        value: app.url,
        description: app.description,
    }));

    return [
        ...appOptions,
        {
            label: 'Pod workspace',
            value: podUrl,
            description: 'Open the pod builder workspace.',
        },
    ];
}

export function buildPodInviteRedirectUri({
    podId,
    podRole,
    apps,
}: {
    podId: string;
    podRole: PodRole;
    apps: InviteApp[];
}): string {
    const isBuilderRole = podRole === PodRole.POD_ADMIN || podRole === PodRole.POD_EDITOR;
    const options = getPodInviteRedirectOptions({ podId, apps });
    const podUrl = toAbsoluteSiteUrl(`/pod/${podId}`);
    const firstAppUrl = options.find((option) => option.value !== podUrl)?.value;

    return isBuilderRole ? podUrl : firstAppUrl || podUrl;
}

export function buildOrganizationInviteRedirectUri({
    orgId,
    role,
}: {
    orgId: string;
    role: OrganizationRole;
}): string {
    const normalizedRole = String(role);
    const isBuilderRole = normalizedRole.endsWith('_OWNER') || normalizedRole.endsWith('_EDITOR');
    const path = isBuilderRole ? `/organizations/${orgId}/settings/members` : '/';

    return toAbsoluteSiteUrl(path);
}
