'use client';

import Link from 'next/link';
import { useRouter } from 'next/navigation';
import { useMemo } from 'react';
import type { ReactNode } from 'react';
import { ChevronDown, LogOut, UserRound } from '@/components/ui/icons';
import { Logo } from '@/components/brand/logo';
import { ThemeToggle } from '@/components/theme/theme-toggle';
import { Avatar, AvatarFallback } from '@/components/ui/avatar';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { useProfile } from '@/lib/hooks/use-user';

export function HomeTopbar({
    actions,
    leftContent,
}: {
    actions?: ReactNode;
    leftContent?: ReactNode;
}) {
    const { data: profile } = useProfile();
    const router = useRouter();

    const initials = useMemo(() => {
        if (profile?.first_name && profile?.last_name) {
            return `${profile.first_name[0]}${profile.last_name[0]}`;
        }

        return profile?.email?.[0]?.toUpperCase() || 'U';
    }, [profile?.email, profile?.first_name, profile?.last_name]);

    // Route to the dedicated /logout screen so the user gets immediate
    // "Signing you out…" feedback while the session is torn down.
    const handleLogout = () => {
        router.push('/logout');
    };

    return (
        <header className="sticky top-0 z-40 bg-[color:color-mix(in_srgb,var(--pod-main-bg)_94%,transparent)] px-4 backdrop-blur supports-[backdrop-filter]:bg-[color:color-mix(in_srgb,var(--pod-main-bg)_86%,transparent)] sm:px-7 lg:px-12">
            <div className="mx-auto flex min-h-16 w-full max-w-6xl items-center justify-between gap-3 py-2">
                <div className="flex min-w-0 items-center gap-4">
                    <Link
                        href="/"
                        aria-label="Go to home"
                        className="shrink-0 rounded-2xl transition-opacity hover:opacity-80 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--action-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-canvas)]"
                    >
                        <Logo size="sm" variant="mark-wordmark" />
                    </Link>
                    {leftContent ? <div className="min-w-0">{leftContent}</div> : null}
                </div>

                <div className="flex min-w-0 items-center justify-end gap-2">
                    {actions}
                    <DropdownMenu>
                        <DropdownMenuTrigger asChild>
                            <button
                                type="button"
                                className="home-topbar-user-button -mr-2 inline-flex min-w-0 items-center gap-2 rounded-lg bg-transparent px-2 py-1.5 text-left transition-colors hover:bg-[color:color-mix(in_srgb,var(--surface-2)_42%,transparent)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--action-primary)] focus-visible:ring-offset-2 focus-visible:ring-offset-[var(--bg-canvas)]"
                            >
                                <Avatar className="h-9 w-9">
                                    <AvatarFallback className="border border-[var(--row-border)] bg-[var(--delight-soft)] text-sm font-semibold text-[var(--text-primary)]">
                                        {initials}
                                    </AvatarFallback>
                                </Avatar>
                                <span className="hidden min-w-0 max-w-40 truncate text-sm font-medium text-[var(--text-primary)] sm:block">
                                    {profile?.first_name ? `${profile.first_name} ${profile.last_name || ''}`.trim() : profile?.email || 'Profile'}
                                </span>
                                <ChevronDown className="h-4 w-4 shrink-0 text-[var(--text-tertiary)]" />
                            </button>
                        </DropdownMenuTrigger>

                        <DropdownMenuContent align="end" className="w-[240px]">
                            <DropdownMenuLabel className="truncate">
                                {profile?.first_name ? `${profile.first_name} ${profile.last_name || ''}`.trim() : profile?.email || 'Profile'}
                            </DropdownMenuLabel>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem asChild>
                                <Link href="/profile" className="flex items-center gap-2">
                                    <UserRound className="h-4 w-4" />
                                    Profile
                                </Link>
                            </DropdownMenuItem>
                            <div className="flex items-center justify-between gap-3 px-2 py-1.5 text-sm">
                                <span className="text-[var(--text-secondary)]">Theme</span>
                                <ThemeToggle
                                    variant="icon"
                                    className="h-8 w-8 border border-[color:var(--button-secondary-border)] bg-[var(--button-secondary-bg)] text-[var(--button-secondary-fg)] hover:border-[var(--border-strong)] hover:bg-[var(--button-secondary-bg-hover)] hover:text-[var(--text-primary)]"
                                />
                            </div>
                            <DropdownMenuSeparator />
                            <DropdownMenuItem onClick={() => void handleLogout()}>
                                <LogOut className="mr-2 h-4 w-4" />
                                Logout
                            </DropdownMenuItem>
                        </DropdownMenuContent>
                    </DropdownMenu>
                </div>
            </div>
        </header>
    );
}
