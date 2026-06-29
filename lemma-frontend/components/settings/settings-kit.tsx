'use client';

/**
 * Shared settings kit.
 *
 * One canonical composition language for every settings surface — pod-level and
 * organization-level alike. Both areas previously hand-rolled their own panels,
 * section dividers, and "who can join" controls, which is how they drifted into
 * looking like two different products. Everything here wraps the existing
 * `settings-*` utility classes (see styles/utilities.css); it adds no new CSS,
 * it just stops each page from re-inventing the structure.
 *
 * The page shells stay area-specific on purpose: pod settings render inside the
 * pod sidebar chrome (PodSettingsShell), org settings render as top-level pages
 * with the home topbar (PlainPageShell). Only the *content* is unified here.
 */

import type { ReactNode } from 'react';
import { Check } from 'lucide-react';

import { ResourcePanel, ResourcePanelHeader } from '@/components/pod/resource-layout';
import { cn } from '@/lib/utils';

/**
 * Vertical rhythm wrapper for a settings page body. Spacing only — page width is
 * owned by the page shell (PlainPageShell.contentWidthClassName for org pages)
 * so every tab in an area shares one left edge and one column width.
 */
export function SettingsStack({
    children,
    className,
}: {
    children: ReactNode;
    className?: string;
}) {
    return <div className={cn('settings-stack', className)}>{children}</div>;
}

/**
 * The canonical settings section: a titled card with an optional header action
 * and a padded body. This is the single container both pod and org settings use
 * for a "section" — replacing pod's PodSettingsPanel and org's
 * `settings-open-section` dividers.
 */
export function SettingsPanel({
    title,
    description,
    action,
    children,
    className,
    bodyClassName,
}: {
    title: ReactNode;
    description?: ReactNode;
    action?: ReactNode;
    children: ReactNode;
    className?: string;
    bodyClassName?: string;
}) {
    return (
        <ResourcePanel className={cn('overflow-hidden', className)}>
            <ResourcePanelHeader title={title} description={description} action={action} />
            <div className={cn('px-4 py-4', bodyClassName)}>{children}</div>
        </ResourcePanel>
    );
}

export type SettingsChoiceOption<TValue extends string> = {
    value: TValue;
    label: ReactNode;
    description?: ReactNode;
    /** Optional content revealed beneath the row while it is the selected option. */
    expanded?: ReactNode;
    disabled?: boolean;
};

/**
 * Radio-style choice list with the green-check selected affordance. Extracted
 * from the pod "Who can join" panel so org access controls stop diverging into a
 * bare <Select>. Selection is reported via onChange; persistence (save-on-select
 * vs. an explicit Save button) is left to the caller.
 */
export function SettingsChoiceList<TValue extends string>({
    options,
    value,
    onChange,
    disabled = false,
    ariaLabel,
    className,
}: {
    options: SettingsChoiceOption<TValue>[];
    value: TValue;
    onChange: (value: TValue) => void;
    disabled?: boolean;
    ariaLabel?: string;
    className?: string;
}) {
    return (
        <div className={cn('settings-list', className)} role="radiogroup" aria-label={ariaLabel}>
            {options.map((option) => {
                const selected = option.value === value;
                const rowDisabled = disabled || option.disabled;
                return (
                    <div key={option.value} className="flex flex-col">
                        <button
                            type="button"
                            role="radio"
                            aria-checked={selected}
                            disabled={rowDisabled}
                            onClick={() => {
                                if (!selected) onChange(option.value);
                            }}
                            data-selected={selected}
                            className="settings-choice-row items-start disabled:cursor-not-allowed disabled:opacity-60"
                        >
                            <span className="flex min-w-0 flex-col gap-0.5">
                                <span className="text-sm font-medium text-[var(--text-primary)]">{option.label}</span>
                                {option.description ? (
                                    <span className="text-xs leading-5 text-[var(--text-tertiary)]">
                                        {option.description}
                                    </span>
                                ) : null}
                            </span>
                            <span
                                aria-hidden
                                className={cn(
                                    'mt-0.5 flex h-4 w-4 shrink-0 items-center justify-center rounded-full border transition-gentle',
                                    selected
                                        ? 'border-[var(--state-success)] bg-[var(--state-success)] text-[var(--text-on-brand)]'
                                        : 'border-[var(--field-border)] text-transparent',
                                )}
                            >
                                <Check className="h-3 w-3" strokeWidth={3} />
                            </span>
                        </button>
                        {selected && option.expanded ? (
                            <div className="mt-2 pl-3">{option.expanded}</div>
                        ) : null}
                    </div>
                );
            })}
        </div>
    );
}

/** Responsive strip of stat boxes (URL slug / email domain / join policy, etc.). */
export function SettingsStatStrip({
    children,
    className,
}: {
    children: ReactNode;
    className?: string;
}) {
    return <div className={cn('settings-stat-strip', className)}>{children}</div>;
}

export function SettingsStat({ label, value }: { label: ReactNode; value: ReactNode }) {
    return (
        <div className="settings-stat">
            <p className="type-eyebrow text-[var(--text-tertiary)]">{label}</p>
            <p className="mt-1 break-all text-sm font-medium text-[var(--text-primary)]">{value}</p>
        </div>
    );
}

/** List container for member/invitation-style rows inside a settings panel. */
export function SettingsList({
    children,
    className,
}: {
    children: ReactNode;
    className?: string;
}) {
    return <div className={cn('settings-list', className)}>{children}</div>;
}

export function SettingsRow({
    children,
    className,
    stacked = false,
}: {
    children: ReactNode;
    className?: string;
    stacked?: boolean;
}) {
    return (
        <div className={cn('settings-list-row', stacked && 'settings-list-row-stacked', className)}>
            {children}
        </div>
    );
}

/** Help/empty caption used under controls and lists, e.g. permission notes. */
export function SettingsHelpText({
    children,
    className,
}: {
    children: ReactNode;
    className?: string;
}) {
    return <p className={cn('settings-help-text', className)}>{children}</p>;
}
