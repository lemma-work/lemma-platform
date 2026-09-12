'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { BarChart3, Boxes, Receipt, SlidersHorizontal, Users } from '@/components/ui/icons';
import { useBillingAvailable } from '@/lib/billing/use-billing';
import { ORG_SETTINGS_SECTIONS, orgSettingsHref } from './org-settings-sections';

const SECTION_ICONS = {
    users: Users,
    chart: BarChart3,
    sliders: SlidersHorizontal,
    boxes: Boxes,
} as const;

/**
 * The sections of the organization you are inside.
 *
 * These used to be a strip of tabs above the page, which left the sidebar
 * saying which organization you were in and then stopping. Pods are a section
 * here rather than an expanded list: an organization with a dozen pods pushed
 * everything else out of the rail, and a list that long wants a page.
 */
export function OrgSidebarSection({
    organizationId,
    organizationName,
    onNavigate,
}: {
    organizationId: string;
    organizationName?: string;
    onNavigate: () => void;
}) {
    const pathname = usePathname();
    const { available: billingAvailable } = useBillingAvailable();

    const sections = [
        ...ORG_SETTINGS_SECTIONS.map((section) => ({
            href: orgSettingsHref(organizationId, section.segment),
            label: section.label,
            Icon: SECTION_ICONS[section.icon],
        })),
        // Absent on a self-hosted install, whose backend has no /billing router.
        ...(billingAvailable
            ? [
                  {
                      href: orgSettingsHref(organizationId, 'billing'),
                      label: 'Billing',
                      Icon: Receipt,
                  },
              ]
            : []),
    ];

    return (
        <div className="mt-5">
            <p
                className="type-eyebrow flex h-7 items-center truncate px-2 text-[var(--text-tertiary)]"
                title={organizationName}
            >
                {organizationName || 'Organization'}
            </p>
            <nav className="mt-1 space-y-0.5" aria-label="Organization settings">
                {sections.map(({ href, label, Icon }) => (
                    <Link
                        key={href}
                        href={href}
                        onClick={onNavigate}
                        data-active={pathname === href ? 'true' : undefined}
                        className="lemma-sidebar-row lemma-sidebar-row-comfy"
                    >
                        <Icon className="h-4 w-4 shrink-0" />
                        <span className="truncate">{label}</span>
                    </Link>
                ))}
            </nav>
        </div>
    );
}
