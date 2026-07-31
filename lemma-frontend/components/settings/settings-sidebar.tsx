'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import { Building2, Home, Plus, User } from '@/components/ui/icons';

import { Logo } from '@/components/brand/logo';
import { useOrganization } from '@/components/dashboard/org-context';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { Sheet, SheetContent, SheetTitle, SheetTrigger } from '@/components/ui/sheet';
import { PanelLeftOpen } from '@/components/ui/icons';
import { useProfile } from '@/lib/hooks/use-user';

function SettingsSidebarContent({ onNavigate }: { onNavigate: () => void }) {
    const pathname = usePathname();
    const { organizations, setCurrentOrg, isLoading } = useOrganization();
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
                <nav className="space-y-0.5" aria-label="Settings navigation">
                    <Link
                        href="/home"
                        onClick={onNavigate}
                        className="lemma-sidebar-row lemma-sidebar-row-sm"
                    >
                        <Home className="h-4 w-4" />
                        Home
                    </Link>
                    <Link
                        href="/profile"
                        onClick={onNavigate}
                        data-active={pathname === '/profile' ? 'true' : undefined}
                        className="lemma-sidebar-row lemma-sidebar-row-sm"
                    >
                        <User className="h-4 w-4" />
                        Profile
                    </Link>
                </nav>

                <div className="mt-6">
                    <div className="flex h-7 items-center justify-between px-2">
                        <p className="type-eyebrow text-[var(--text-tertiary)]">Organizations</p>
                        <Link
                            href="/organizations/new"
                            onClick={onNavigate}
                            className="custom-focus-ring flex h-6 w-6 items-center justify-center rounded-md text-[var(--text-tertiary)] transition-colors hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)]"
                            aria-label="Create organization"
                            title="Create organization"
                        >
                            <Plus className="h-3.5 w-3.5" />
                        </Link>
                    </div>

                    <div className="mt-1 space-y-0.5">
                        {isLoading ? (
                            <p className="px-2 py-2 text-xs text-[var(--text-tertiary)]">Loading organizations…</p>
                        ) : organizations.length === 0 ? (
                            <p className="px-2 py-2 text-xs text-[var(--text-tertiary)]">No organizations yet.</p>
                        ) : organizations.map((organization) => {
                            const active = pathname.startsWith(`/organizations/${organization.id}/`);

                            return (
                                <Link
                                    key={organization.id}
                                    href={`/organizations/${organization.id}/settings/members`}
                                    onClick={() => {
                                        setCurrentOrg(organization);
                                        onNavigate();
                                    }}
                                    data-active={active ? 'true' : undefined}
                                    className="lemma-sidebar-row lemma-sidebar-row-sm min-w-0"
                                    title={organization.name}
                                >
                                    <Building2 className="h-4 w-4 shrink-0" />
                                    <span className="truncate">{organization.name}</span>
                                </Link>
                            );
                        })}
                    </div>
                </div>
            </div>

            <div className="flex shrink-0 items-center gap-2 border-t border-[color:color-mix(in_srgb,var(--border-subtle)_42%,transparent)] px-2.5 py-2">
                <Avatar className="h-7 w-7 shrink-0 border border-[var(--border-subtle)]">
                    <AvatarFallback className="bg-[var(--surface-2)] text-xs text-[var(--text-secondary)]">
                        {initials}
                    </AvatarFallback>
                </Avatar>
                <span className="min-w-0 flex-1 truncate text-sm text-[var(--text-primary)]" title={displayName}>
                    {displayName}
                </span>
                <ThemeToggle variant="icon" className="lemma-shell-icon-button h-8 w-8 shrink-0" />
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
