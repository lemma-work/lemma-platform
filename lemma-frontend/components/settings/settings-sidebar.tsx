'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

import { Logo } from '@/components/brand/logo';
import { LocalSettingsButton } from '@/components/desktop/local-settings-button';
import { useOrganization } from '@/components/dashboard/org-context';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { PanelLeftOpen } from '@/components/ui/icons';
import { useProfile } from '@/lib/hooks/use-user';
import { OrgSidebarSection } from './org-sidebar-section';
import { OrgSwitcher } from './org-switcher';

/** The organization whose settings the current route belongs to, if any. */
function activeOrganizationIdFrom(pathname: string): string | undefined {
    return pathname.match(/^\/organizations\/([^/]+)\//)?.[1];
}

function SettingsSidebarContent({ onNavigate }: { onNavigate: () => void }) {
    const pathname = usePathname();
    const activeOrganizationId = activeOrganizationIdFrom(pathname);
    const { organizations, currentOrg } = useOrganization();
    // Off an organization route -- on your profile, say -- the rail still shows
    // the organization you are working in, so it is not a switcher above a
    // column of nothing.
    const shownOrganizationId = activeOrganizationId ?? currentOrg?.id;
    const { data: profile } = useProfile();
    const displayName = profile?.first_name
        ? `${profile.first_name} ${profile.last_name || ''}`.trim()
        : profile?.email?.split('@')[0] || 'Account';
    const initials = profile?.first_name && profile?.last_name
        ? `${profile.first_name[0]}${profile.last_name[0]}`
        : profile?.email?.[0]?.toUpperCase() || 'U';

    return (
        <div className="flex h-full min-h-0 flex-col bg-[var(--pod-shell-bg)] text-[var(--text-secondary)]">
            <div className="flex h-12 shrink-0 items-center border-b border-[color:color-mix(in_srgb,var(--border-subtle)_42%,transparent)] px-3">
                <Link
                    href="/home"
                    onClick={onNavigate}
                    className="custom-focus-ring inline-flex h-8 items-center rounded-md px-1 transition-colors hover:bg-[var(--surface-2)]"
                    aria-label="Go to Lemma home"
                >
                    <Logo size="xs" variant="mark-wordmark" />
                </Link>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto px-2.5 py-3">
                {/* One row for the organization you are in, rather than a list
                    of every organization you belong to. */}
                <OrgSwitcher
                    activeOrganizationId={activeOrganizationId}
                    onNavigate={onNavigate}
                />

                {shownOrganizationId ? (
                    <OrgSidebarSection
                        organizationId={shownOrganizationId}
                        organizationName={
                            organizations.find((org) => org.id === shownOrganizationId)?.name
                        }
                        onNavigate={onNavigate}
                    />
                ) : null}
            </div>

            <div className="shrink-0 border-t border-[color:color-mix(in_srgb,var(--border-subtle)_42%,transparent)] px-2.5 py-2">
                <LocalSettingsButton className="mb-1" />
                {/* One row: who you are, and the theme control at the end of
                    it. Splitting them left the toggle floating on a line of
                    its own under your name. */}
                <div className="flex items-center gap-1">
                    <Link
                        href="/profile"
                        onClick={onNavigate}
                        data-active={pathname === '/profile' ? 'true' : undefined}
                        className="lemma-sidebar-row lemma-sidebar-row-comfy min-w-0 flex-1"
                        title={`${displayName} — open profile`}
                    >
                        <Avatar className="h-6 w-6 shrink-0 border border-[var(--border-subtle)]">
                            <AvatarFallback className="bg-[var(--surface-2)] text-xs text-[var(--text-secondary)]">
                                {initials}
                            </AvatarFallback>
                        </Avatar>
                        <span className="truncate">{displayName}</span>
                    </Link>
                    <ThemeToggle variant="icon" className="lemma-shell-icon-button h-8 w-8 shrink-0" />
                </div>
            </div>
        </div>
    );
}

export function SettingsSidebar() {
    return <SettingsSidebarContent onNavigate={() => {}} />;
}

export function SettingsMobileSidebar({
    open,
    onOpenChange,
}: {
    open: boolean;
    onOpenChange: (open: boolean) => void;
}) {
    return (
        <div className="shrink-0 md:hidden">
            <Sheet open={open} onOpenChange={onOpenChange}>
                <SheetTrigger asChild>
                    <button
                        type="button"
                        className="lemma-shell-icon-button custom-focus-ring flex h-8 w-8 items-center justify-center text-[var(--text-secondary)]"
                        aria-label="Open settings navigation"
                    >
                        <PanelLeftOpen className="h-4 w-4" />
                    </button>
                </SheetTrigger>
                <SheetContent
                    side="left"
                    className="w-[min(18rem,88vw)] border-r border-[var(--row-border)] bg-[var(--pod-shell-bg)] p-0 shadow-none"
                >
                    <SheetTitle className="sr-only">Settings navigation</SheetTitle>
                    <SettingsSidebarContent onNavigate={() => onOpenChange(false)} />
                </SheetContent>
            </Sheet>
        </div>
    );
}
