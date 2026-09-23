'use client';

import type { ReactNode } from 'react';

/**
 * The title band for a settings page.
 *
 * The shell's context bar is a thin strip that carries the back link and the
 * section name at nav altitude; this is the page's own statement of what you
 * are looking at, set large enough to anchor a screen of cards. It exists
 * because an organization's sections moved into the sidebar: with the tab strip
 * gone, nothing in the content area said where you had landed.
 */
export function SettingsPageHeading({
    title,
    description,
    action,
}: {
    title: ReactNode;
    description?: ReactNode;
    action?: ReactNode;
}) {
    return (
        <div className="flex flex-wrap items-start justify-between gap-3">
            <div className="min-w-0 space-y-1">
                <h1 className="text-2xl text-[var(--text-primary)]">{title}</h1>
                {description ? (
                    <p className="text-sm text-[var(--text-secondary)]">{description}</p>
                ) : null}
            </div>
            {action ? <div className="shrink-0">{action}</div> : null}
        </div>
    );
}
