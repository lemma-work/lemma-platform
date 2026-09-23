'use client';

import { useEffect, type ReactNode } from 'react';
import { cn } from '@/lib/utils';

/**
 * Off-canvas drawer that hosts the workspace sidebar below the `md` breakpoint,
 * where the inline sidebar slot is hidden.
 */
export function MobileSidebarDrawer({
    isOpen,
    onClose,
    children,
}: {
    isOpen: boolean;
    onClose: () => void;
    children: ReactNode;
}) {
    // Close on Escape
    useEffect(() => {
        if (!isOpen) return;
        const handleKeyDown = (e: KeyboardEvent) => {
            if (e.key === 'Escape') onClose();
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, [isOpen, onClose]);

    // Lock body scroll while open
    useEffect(() => {
        if (!isOpen) return;
        const previous = document.body.style.overflow;
        document.body.style.overflow = 'hidden';
        return () => {
            document.body.style.overflow = previous;
        };
    }, [isOpen]);

    // The inline sidebar takes over at md; an open drawer would be orphaned there.
    useEffect(() => {
        if (!isOpen) return;
        const mediaQuery = window.matchMedia('(min-width: 768px)');
        const handleChange = (e: MediaQueryListEvent) => {
            if (e.matches) onClose();
        };
        mediaQuery.addEventListener('change', handleChange);
        return () => mediaQuery.removeEventListener('change', handleChange);
    }, [isOpen, onClose]);

    return (
        <div className="md:hidden">
            {/* Backdrop */}
            <div
                className={cn(
                    "scrim-overlay fixed inset-0 z-40 backdrop-blur-sm transition-opacity duration-200",
                    isOpen ? "opacity-100 pointer-events-auto" : "opacity-0 pointer-events-none"
                )}
                onClick={onClose}
                aria-hidden="true"
            />

            {/* Sidebar wrapper */}
            <div
                className={cn(
                    "fixed inset-y-0 left-0 z-50 w-[280px] transition-transform duration-200 ease-out",
                    isOpen ? "translate-x-0" : "-translate-x-full"
                )}
                role="dialog"
                aria-modal="true"
                aria-label="Pod navigation"
            >
                {children}
            </div>
        </div>
    );
}
