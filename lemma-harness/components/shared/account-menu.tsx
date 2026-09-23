'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useSyncExternalStore } from 'react';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';
import { useTheme } from 'next-themes';

import {
    BarChart3,
    Building2,
    ChevronDown,
    DiscordLogo,
    ExternalLink,
    GithubLogo,
    Home,
    LogOut,
    Moon,
    Settings,
    Sun,
    User,
} from '@/components/ui/icons';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import { useOrganization } from '@/components/dashboard/org-context';
import { DISCORD_INVITE_URL, GITHUB_REPO_URL } from '@/lib/community-links';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePod } from '@/lib/hooks/use-pods';
import { useProfile } from '@/lib/hooks/use-user';
import { cn } from '@/lib/utils';

interface AccountMenuProps {
    /** When present, the menu also carries the places that belong to that pod. */
    podId?: string;
    /** 'row' fills the sidebar footer; 'rail' is the avatar-only collapsed form. */
    variant?: 'row' | 'rail';
    side?: 'top' | 'right' | 'bottom';
    align?: 'start' | 'end';
    className?: string;
    /** Drawers and sheets close themselves once you pick a destination. */
    onNavigate?: () => void;
}

/**
 * Every place an account can go, named. The footer used to be a strip of icon
 * buttons whose destinations you had to learn by clicking them; this is the one
 * picker that says where each one lands, so nothing down there is a guess.
 */
export function AccountMenu({
    podId,
    variant = 'row',
    side = 'top',
    align = 'start',
    className,
    onNavigate,
}: AccountMenuProps) {
    const router = useRouter();
    const { data: profile } = useProfile();
    const { currentOrg } = useOrganization();
    const { data: pod } = usePod(podId);
    const podAccess = usePodAccess(podId);

    // The pod owns the organization when we are inside one; outside a pod the
    // account's selected org is the only one in scope.
    const organizationId = pod?.organization_id || currentOrg?.id;
    const canUsePodSettings = Boolean(podId) && podAccess.canAccessRoute('settings');

    const initials = profile?.first_name && profile?.last_name
        ? `${profile.first_name[0]}${profile.last_name[0]}`
        : profile?.email?.[0]?.toUpperCase() || 'U';
    const fullName = profile?.first_name
        ? `${profile.first_name} ${profile.last_name || ''}`.trim()
        : '';
    const displayName = fullName || profile?.email?.split('@')[0] || 'Account';
    const hasIdentity = Boolean(fullName || profile?.email);

    // Route to the dedicated /logout screen so the user gets immediate
    // "Signing you out…" feedback while the session is torn down.
    const handleLogout = () => {
        onNavigate?.();
        router.push('/logout');
    };

    return (
        <DropdownMenu.Root>
            <DropdownMenu.Trigger asChild>
                {variant === 'rail' ? (
                    <button
                        type="button"
                        className={cn('lemma-sidebar-rail-icon', className)}
                        aria-label={`Open account menu for ${displayName}`}
                        title={displayName}
                    >
                        <Avatar className="h-7 w-7 shrink-0">
                            <AvatarFallback className="border border-[color:var(--chip-border)] bg-[var(--chip-bg)] text-xs font-semibold text-[var(--action-primary)]">
                                {profile ? initials : <User className="h-4 w-4" />}
                            </AvatarFallback>
                        </Avatar>
                    </button>
                ) : (
                    <button
                        type="button"
                        className={cn(
                            'workspace-sidebar-trigger-button custom-focus-ring flex h-9 min-w-0 flex-1 items-center gap-2 rounded-lg px-1.5 text-left transition-colors hover:bg-[var(--surface-2)] data-[state=open]:bg-[var(--surface-2)]',
                            className,
                        )}
                        aria-label={`Open account menu for ${displayName}`}
                        title={displayName}
                    >
                        <Avatar className="h-7 w-7 shrink-0 border border-[var(--border-subtle)]">
                            <AvatarFallback className="bg-[var(--surface-2)] text-xs text-[var(--text-secondary)]">
                                {profile ? initials : <User className="h-4 w-4" />}
                            </AvatarFallback>
                        </Avatar>
                        <span className="min-w-0 flex-1 truncate text-sm font-medium text-[var(--text-primary)]">
                            {displayName}
                        </span>
                        <ChevronDown className="h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
                    </button>
                )}
            </DropdownMenu.Trigger>

            <DropdownMenu.Portal>
                <DropdownMenu.Content
                    align={align}
                    side={side}
                    sideOffset={8}
                    className="surface-panel z-50 w-64 p-1 shadow-[var(--shadow-lg)]"
                >
                    {/* Groups carry a trailing rule rather than a leading one, so a
                        profile that has not loaded yet leaves no empty band behind. */}
                    {hasIdentity ? (
                        <>
                            <div className="px-2 py-1.5">
                                {fullName ? (
                                    <p className="truncate text-sm font-medium text-[var(--text-primary)]">{fullName}</p>
                                ) : null}
                                {profile?.email ? (
                                    <p className="truncate text-xs text-[var(--text-tertiary)]">{profile.email}</p>
                                ) : null}
                            </div>
                            <DropdownMenu.Separator className="my-1 h-px bg-[var(--border-subtle)]" />
                        </>
                    ) : null}

                    {canUsePodSettings ? (
                        <>
                            <MenuLink
                                href={`/pod/${podId}/settings`}
                                icon={<Settings className="h-4 w-4 text-[var(--text-tertiary)]" />}
                                label="Pod settings"
                                onNavigate={onNavigate}
                            />
                            <MenuLink
                                href={`/pod/${podId}/settings/usage`}
                                icon={<BarChart3 className="h-4 w-4 text-[var(--text-tertiary)]" />}
                                label="Usage"
                                onNavigate={onNavigate}
                            />
                            <DropdownMenu.Separator className="my-1 h-px bg-[var(--border-subtle)]" />
                        </>
                    ) : null}

                    <MenuLink
                        href="/home"
                        icon={<Home className="h-4 w-4 text-[var(--text-tertiary)]" />}
                        label="All pods"
                        onNavigate={onNavigate}
                    />
                    {organizationId ? (
                        <MenuLink
                            href={`/organizations/${organizationId}/settings/members`}
                            icon={<Building2 className="h-4 w-4 text-[var(--text-tertiary)]" />}
                            label="Organization settings"
                            onNavigate={onNavigate}
                        />
                    ) : null}
                    {/* Outside a pod, usage is the organization's bill — inside one
                        it is already listed above, scoped to that pod. */}
                    {!canUsePodSettings && organizationId ? (
                        <MenuLink
                            href={`/organizations/${organizationId}/settings/usage`}
                            icon={<BarChart3 className="h-4 w-4 text-[var(--text-tertiary)]" />}
                            label="Usage"
                            onNavigate={onNavigate}
                        />
                    ) : null}
                    <MenuLink
                        href="/profile"
                        icon={<User className="h-4 w-4 text-[var(--text-tertiary)]" />}
                        label="Profile"
                        onNavigate={onNavigate}
                    />

                    <DropdownMenu.Separator className="my-1 h-px bg-[var(--border-subtle)]" />
                    <MenuLink
                        href={GITHUB_REPO_URL}
                        external
                        icon={<GithubLogo className="h-4 w-4 text-[var(--text-tertiary)]" />}
                        label="Star on GitHub"
                        onNavigate={onNavigate}
                    />
                    <MenuLink
                        href={DISCORD_INVITE_URL}
                        external
                        icon={<DiscordLogo className="h-4 w-4 text-[var(--text-tertiary)]" />}
                        label="Join the Discord"
                        onNavigate={onNavigate}
                    />

                    <DropdownMenu.Separator className="my-1 h-px bg-[var(--border-subtle)]" />
                    <ThemeMenuItem />

                    <DropdownMenu.Separator className="my-1 h-px bg-[var(--border-subtle)]" />
                    <DropdownMenu.Item
                        onSelect={handleLogout}
                        className="hover-state-error focus-state-error flex cursor-pointer items-center gap-2 rounded-md px-2 py-2 text-sm text-[var(--state-error)] outline-none transition-colors"
                    >
                        <LogOut className="h-4 w-4" />
                        Log out
                    </DropdownMenu.Item>
                </DropdownMenu.Content>
            </DropdownMenu.Portal>
        </DropdownMenu.Root>
    );
}

function MenuLink({
    href,
    icon,
    label,
    external,
    onNavigate,
}: {
    href: string;
    icon: React.ReactNode;
    label: string;
    external?: boolean;
    onNavigate?: () => void;
}) {
    return (
        <DropdownMenu.Item asChild>
            <Link
                href={href}
                onClick={onNavigate}
                {...(external ? { target: '_blank', rel: 'noreferrer noopener' } : {})}
                className="lemma-menu-row lemma-menu-row-between"
            >
                <span className="flex min-w-0 items-center gap-2">
                    {icon}
                    <span className="truncate">{label}</span>
                </span>
                {external ? (
                    <ExternalLink className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" aria-hidden="true" />
                ) : null}
            </Link>
        </DropdownMenu.Item>
    );
}

/**
 * The theme control says which way it will move rather than which way it sits.
 * Selecting it keeps the menu open so the change is visible where you made it.
 */
function ThemeMenuItem() {
    const { resolvedTheme, setTheme } = useTheme();
    const mounted = useSyncExternalStore(
        () => () => { },
        () => true,
        () => false,
    );

    const isDark = mounted && resolvedTheme === 'dark';
    const Icon = isDark ? Sun : Moon;

    return (
        <DropdownMenu.Item
            onSelect={(event) => {
                event.preventDefault();
                setTheme(isDark ? 'light' : 'dark');
            }}
            className="lemma-menu-row"
        >
            <Icon className="h-4 w-4 text-[var(--text-tertiary)]" />
            {isDark ? 'Switch to light theme' : 'Switch to dark theme'}
        </DropdownMenu.Item>
    );
}
