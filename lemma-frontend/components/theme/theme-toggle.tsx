'use client';

import { useTheme } from 'next-themes';
import { useSyncExternalStore } from 'react';
import { cn } from '@/lib/utils';
import { Moon, Sun } from '@/components/ui/icons';

interface ThemeToggleProps {
    className?: string;
    variant?: 'pills' | 'icon';
}

export function ThemeToggle({ className, variant = 'pills' }: ThemeToggleProps) {
    const { resolvedTheme, setTheme } = useTheme();
    const mounted = useSyncExternalStore(
        () => () => { },
        () => true,
        () => false
    );

    const isDark = mounted && resolvedTheme === 'dark';

    if (variant === 'icon') {
        const targetTheme = isDark ? 'light' : 'dark';
        const title = isDark ? 'Switch to light mode' : 'Switch to dark mode';
        const Icon = isDark ? Sun : Moon;

        return (
            <button
                type="button"
                className={cn(
                    'theme-toggle-icon-button inline-flex h-8 w-8 items-center justify-center rounded-md border border-[color:var(--border-subtle)] text-[var(--text-tertiary)] transition-gentle hover:bg-[var(--bg-subtle)] hover:text-[var(--text-primary)]',
                    className
                )}
                aria-label={title}
                title={title}
                onClick={() => setTheme(targetTheme)}
            >
                <Icon className="h-4 w-4" />
            </button>
        );
    }

    return (
        <div className={cn("inline-flex items-center gap-1 rounded-full border border-[color:var(--border-subtle)] bg-[var(--bg-subtle)] p-1", className)}>
            <button
                type="button"
                className={cn(
                    "theme-toggle-pill-button rounded-full px-3 py-1 type-micro-label transition-gentle",
                    !isDark
                        ? "bg-[var(--surface-1)] text-[var(--text-primary)] shadow-[var(--shadow-xs)]"
                        : "text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                )}
                aria-label="Switch to light mode"
                title="Switch to light mode"
                onClick={() => setTheme('light')}
            >
                Light
            </button>
            <button
                type="button"
                className={cn(
                    "theme-toggle-pill-button rounded-full px-3 py-1 type-micro-label transition-gentle",
                    isDark
                        ? "bg-[var(--surface-1)] text-[var(--text-primary)] shadow-[var(--shadow-xs)]"
                        : "text-[var(--text-tertiary)] hover:text-[var(--text-primary)]"
                )}
                aria-label="Switch to dark mode"
                title="Switch to dark mode"
                onClick={() => setTheme('dark')}
            >
                Dark
            </button>
        </div>
    );
}
