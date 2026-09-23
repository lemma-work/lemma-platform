'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';

import { Building2, Check, ChevronDown, Plus } from '@/components/ui/icons';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useOrganization } from '@/components/dashboard/org-context';
import type { Organization } from '@/lib/types';

/**
 * Which organization you are working in, as one control.
 *
 * This was a flat list of every organization, which is fine at two and unusable
 * at seven: the list pushed the sections you actually came for below the fold,
 * and long names (several of ours are email addresses) wrapped the rail. A
 * switcher spends one row on the organization you are in and hides the rest
 * until you want them.
 */
export function OrgSwitcher({
    activeOrganizationId,
    onNavigate,
}: {
    activeOrganizationId?: string;
    onNavigate: () => void;
}) {
    const router = useRouter();
    const { organizations, setCurrentOrg, currentOrg, isLoading } = useOrganization();

    const active =
        organizations.find((org) => org.id === activeOrganizationId) ?? currentOrg ?? null;

    const choose = (organization: Organization) => {
        setCurrentOrg(organization);
        onNavigate();
        router.push(`/organizations/${organization.id}/settings/members`);
    };

    if (isLoading) {
        return (
            <div className="lemma-sidebar-row lemma-sidebar-row-comfy text-[var(--text-tertiary)]">
                Loading…
            </div>
        );
    }

    if (!organizations.length) {
        return (
            <Link
                href="/organizations/new"
                onClick={onNavigate}
                className="lemma-sidebar-row lemma-sidebar-row-comfy"
            >
                <Plus className="h-4 w-4 shrink-0" />
                <span className="truncate">New organization</span>
            </Link>
        );
    }

    return (
        <div className="space-y-1">
            {/* Named rather than implied: a lone chevron on a row that is also
                the current organization reads as a label, not a control. */}
            <p className="type-eyebrow px-1 text-[var(--text-tertiary)]">Switch org</p>
            <DropdownMenu>
                <DropdownMenuTrigger asChild>
                <button
                    type="button"
                    className="lemma-sidebar-row lemma-sidebar-row-comfy lemma-sidebar-row-between custom-focus-ring border border-[color:var(--border-subtle)] bg-[var(--surface-1)]"
                    aria-label="Switch organization"
                >
                    <span className="flex min-w-0 items-center gap-3">
                        <span
                            aria-hidden
                            className="flex h-6 w-6 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)] text-xs text-[var(--text-secondary)]"
                        >
                            {(active?.name ?? '?').slice(0, 1).toUpperCase()}
                        </span>
                        <span className="truncate" title={active?.name}>
                            {active?.name ?? 'Choose organization'}
                        </span>
                    </span>
                    <ChevronDown className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                </button>
                </DropdownMenuTrigger>

                <DropdownMenuContent align="start" className="w-64">
                {organizations.map((organization) => (
                    <DropdownMenuItem
                        key={organization.id}
                        onSelect={() => choose(organization)}
                        className="gap-2"
                    >
                        <Building2 className="h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
                        <span className="min-w-0 flex-1 truncate" title={organization.name}>
                            {organization.name}
                        </span>
                        {organization.id === active?.id ? (
                            <Check className="h-3.5 w-3.5 shrink-0" strokeWidth={3} />
                        ) : null}
                    </DropdownMenuItem>
                ))}
                <DropdownMenuSeparator />
                <DropdownMenuItem asChild className="gap-2">
                    <Link href="/organizations/new" onClick={onNavigate}>
                        <Plus className="h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
                        New organization
                    </Link>
                </DropdownMenuItem>
                </DropdownMenuContent>
            </DropdownMenu>
        </div>
    );
}
