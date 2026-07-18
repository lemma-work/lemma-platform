'use client';

import type { ButtonHTMLAttributes, ReactNode } from 'react';
import { forwardRef } from 'react';
import { useLayoutEffect } from 'react';

import type { ProductIconKind } from '@/components/pod/product-icon';
import { usePodTopbar } from '@/components/pod/pod-topbar-context';
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { cn } from '@/lib/utils';

export type ResourceTabItem<TValue extends string = string> = {
    value: TValue;
    label: string;
};

export type ResourceLayoutMode = 'ledger' | 'workbench' | 'inspector' | 'builder';

export const ResourceTitleButton = forwardRef<HTMLButtonElement, ButtonHTMLAttributes<HTMLButtonElement> & {
    icon?: ReactNode;
    trailing?: ReactNode;
}>(
function ResourceTitleButton({
    icon,
    trailing,
    children,
    className,
    type = 'button',
    ...props
}, ref) {
    return (
        <button
            ref={ref}
            type={type}
            className={cn(
                'resource-title-button custom-focus-ring flex h-7 min-w-0 items-center gap-2 rounded-md px-1.5 text-left text-lg font-medium leading-7 text-[var(--text-primary)] transition-colors hover:bg-[var(--surface-2)]',
                className
            )}
            {...props}
        >
            {icon ? <span className="flex h-5 w-5 shrink-0 items-center justify-center text-[var(--text-tertiary)]">{icon}</span> : null}
            <span className="min-w-0 truncate leading-7">
                {children}
            </span>
            {trailing ? <span className="shrink-0 text-[var(--text-tertiary)]">{trailing}</span> : null}
        </button>
    );
});

export function ResourceDetailShell({
    children,
    className,
}: {
    children: ReactNode;
    className?: string;
}) {
    return (
        <div className={cn('flex h-full min-h-0 flex-col bg-[var(--bg-canvas)]', className)}>
            {children}
        </div>
    );
}

export function ResourceIndexShell({
    children,
    mode = 'ledger',
    className,
}: {
    children: ReactNode;
    mode?: Extract<ResourceLayoutMode, 'ledger' | 'workbench'>;
    className?: string;
}) {
    if (mode === 'workbench') {
        return (
            <div className={cn('resource-workbench-shell context-shell flex h-full min-h-0 flex-col bg-transparent', className)}>
                {children}
            </div>
        );
    }

    return (
        <div className={cn('resource-index-shell context-shell min-h-full bg-transparent', className)}>
            {children}
        </div>
    );
}

export function ResourceIndexHeader({
    title,
    productIconKind,
    icon,
    backHref,
    backLabel,
    eyebrow,
    meta,
    actions,
}: {
    title: ReactNode;
    productIconKind?: ProductIconKind;
    icon?: ReactNode;
    backHref?: string;
    backLabel?: string;
    eyebrow?: ReactNode;
    meta?: ReactNode;
    actions?: ReactNode;
    className?: string;
}) {
    const topbar = usePodTopbar();

    useLayoutEffect(() => {
        topbar?.setTopbar({ title, backHref, backLabel, eyebrow, meta, actions });
        return () => topbar?.setTopbar({});
    }, [actions, backHref, backLabel, eyebrow, meta, title, topbar]);

    void icon;
    void productIconKind;

    return null;
}

export function ResourceObjectHeader({
    title,
    productIconKind,
    icon,
    backHref,
    backLabel,
    meta,
    switcher,
    tabs,
    actions,
    fullscreen,
}: {
    title: ReactNode;
    productIconKind?: ProductIconKind;
    icon?: ReactNode;
    backHref: string;
    backLabel: string;
    meta?: ReactNode;
    switcher?: ReactNode;
    tabs?: ReactNode;
    actions?: ReactNode;
    fullscreen?: boolean;
    className?: string;
}) {
    const topbar = usePodTopbar();

    useLayoutEffect(() => {
        topbar?.setTopbar({ title, backHref, backLabel, meta, switcher, tabs, actions, fullscreen });
        return () => topbar?.setTopbar({});
    }, [actions, backHref, backLabel, fullscreen, meta, switcher, tabs, title, topbar]);

    void icon;
    void productIconKind;

    return null;
}

export function ResourceDetailHeader({
    title,
    productIconKind,
    icon,
    backHref,
    backLabel,
    meta,
    switcher,
    tabs,
    actions,
    fullscreen,
    className,
}: {
    title: ReactNode;
    productIconKind?: ProductIconKind;
    icon?: ReactNode;
    backHref: string;
    backLabel: string;
    meta?: ReactNode;
    switcher?: ReactNode;
    tabs?: ReactNode;
    actions?: ReactNode;
    fullscreen?: boolean;
    className?: string;
}) {
    return <ResourceObjectHeader title={title} productIconKind={productIconKind} icon={icon} backHref={backHref} backLabel={backLabel} meta={meta} switcher={switcher} tabs={tabs} actions={actions} fullscreen={fullscreen} className={className} />;
}

export function ResourceHeaderTabs<TValue extends string>({
    value,
    onValueChange,
    items,
    className,
}: {
    value: TValue;
    onValueChange: (value: TValue) => void;
    items: ResourceTabItem<TValue>[];
    className?: string;
}) {
    return (
        <Tabs value={value} onValueChange={(next) => onValueChange(next as TValue)} className={className}>
            <TabsList className="h-8 gap-1 border-0 bg-transparent p-0 shadow-none">
                {items.map((item) => (
                    <TabsTrigger
                        key={item.value}
                        value={item.value}
                        className="h-7 rounded-md px-2 text-sm font-medium text-[var(--text-tertiary)] data-[state=active]:bg-[var(--chip-bg)] data-[state=active]:text-[var(--text-primary)] data-[state=active]:shadow-none"
                    >
                        {item.label}
                    </TabsTrigger>
                ))}
            </TabsList>
        </Tabs>
    );
}

export function ResourceDetailViewport({
    children,
    className,
}: {
    children: ReactNode;
    className?: string;
}) {
    return (
        <div className={cn('relative min-h-0 flex-1 overflow-hidden', className)}>
            {children}
        </div>
    );
}

export function ResourceTabPane({
    active,
    children,
    className,
}: {
    active: boolean;
    children: ReactNode;
    className?: string;
}) {
    return (
        <div
            className={cn(
                'absolute inset-0 bg-[var(--bg-canvas)] transition-opacity duration-[var(--dur-panel)] ease-[var(--ease-standard)]',
                active ? 'z-10 opacity-100' : 'pointer-events-none z-0 opacity-0',
                className
            )}
            aria-hidden={!active}
        >
            {active ? children : null}
        </div>
    );
}

export function ResourceContentFrame({
    children,
    width = 'regular',
    mode = 'ledger',
    className,
}: {
    children: ReactNode;
    width?: 'regular' | 'wide';
    mode?: Extract<ResourceLayoutMode, 'ledger' | 'workbench' | 'inspector' | 'builder'>;
    className?: string;
}) {
    const maxWidthClass =
        width === 'wide'
            ? 'max-w-7xl'
            : mode === 'workbench'
                ? 'max-w-none'
                : 'max-w-6xl';

    return (
        <div className="h-full overflow-y-auto">
            <div className={cn('mx-auto px-5 py-4', maxWidthClass, className)}>
                {children}
            </div>
        </div>
    );
}

export function ResourceLayoutGrid({
    mode = 'ledger',
    main,
    aside,
    rail,
    className,
    mainClassName,
    asideClassName,
    railClassName,
}: {
    mode?: ResourceLayoutMode;
    main: ReactNode;
    aside?: ReactNode;
    rail?: ReactNode;
    className?: string;
    mainClassName?: string;
    asideClassName?: string;
    railClassName?: string;
}) {
    if (mode === 'workbench') {
        return (
            <div className={cn('flex h-full min-h-0 flex-col', className)}>
                <div className={cn('min-h-0 flex-1', mainClassName)}>{main}</div>
            </div>
        );
    }

    if (mode === 'builder') {
        return (
            <div className={cn('surface-split-2 grid min-h-0 gap-5 lg:grid-cols-[minmax(0,1fr)_19rem] xl:grid-cols-[minmax(0,1fr)_20rem]', className)}>
                <main className={cn('min-w-0', mainClassName)}>{main}</main>
                {aside ? <aside className={cn('min-w-0 lg:sticky lg:top-4 lg:self-start', asideClassName)}>{aside}</aside> : null}
            </div>
        );
    }

    if (mode === 'inspector') {
        return (
            <div className={cn('surface-split-2 grid min-h-0 gap-8 xl:grid-cols-[minmax(0,1fr)_19rem]', className)}>
                <main className={cn('min-w-0', mainClassName)}>{main}</main>
                {aside ? (
                    <aside className={cn('min-w-0 border-l border-[color:color-mix(in_srgb,var(--border-subtle)_54%,transparent)] pl-5', asideClassName)}>
                        {aside}
                    </aside>
                ) : null}
            </div>
        );
    }

    if (rail || aside) {
        return (
            <div className={cn('surface-split-3 grid min-h-0 gap-5 xl:grid-cols-[14rem_minmax(0,1fr)_18rem]', className)}>
                {rail ? <aside className={cn('min-w-0', railClassName)}>{rail}</aside> : null}
                <main className={cn('min-w-0', mainClassName)}>{main}</main>
                {aside ? <aside className={cn('min-w-0', asideClassName)}>{aside}</aside> : null}
            </div>
        );
    }

    return <main className={cn('min-w-0', className, mainClassName)}>{main}</main>;
}

export function ResourceMetricStrip({
    children,
    className,
}: {
    children: ReactNode;
    className?: string;
}) {
    return (
        <div className={cn('lemma-index-tabs flex-wrap', className)}>
            <div className="flex flex-wrap items-center gap-2 text-sm text-[var(--text-tertiary)]">
                {children}
            </div>
        </div>
    );
}

export function ResourcePanel({
    children,
    className,
}: {
    children: ReactNode;
    className?: string;
}) {
    return (
        <section className={cn('resource-panel rounded-lg border border-[var(--card-border-subtle)] bg-[var(--card-bg)] shadow-[var(--card-shadow)]', className)}>
            {children}
        </section>
    );
}

export function ResourcePanelHeader({
    title,
    description,
    action,
    className,
}: {
    title: ReactNode;
    description?: ReactNode;
    action?: ReactNode;
    className?: string;
}) {
    return (
        <div className={cn('flex flex-col gap-2 border-b border-[var(--card-border-subtle)] px-4 py-3 sm:flex-row sm:items-start sm:justify-between', className)}>
            <div className="min-w-0">
                <h2 className="text-sm font-medium text-[var(--text-primary)]">{title}</h2>
                {description ? (
                    <p className="mt-0.5 max-w-2xl text-xs leading-5 text-[var(--text-secondary)]">
                        {description}
                    </p>
                ) : null}
            </div>
            {action ? <div className="flex shrink-0 items-center gap-2">{action}</div> : null}
        </div>
    );
}

export function ResourceMetric({
    label,
    value,
    active,
}: {
    label: ReactNode;
    value: ReactNode;
    active?: boolean;
}) {
    return (
        <span
            className="lemma-index-tab"
            data-active={active}
        >
            <strong className="font-medium text-[var(--text-primary)]">{value}</strong>
            <span>{label}</span>
        </span>
    );
}

export function ResourceMetricButton({
    active,
    label,
    count,
    onClick,
    className,
}: {
    active: boolean;
    label: ReactNode;
    count: ReactNode;
    onClick: () => void;
    className?: string;
}) {
    return (
        <button
            type="button"
            className={cn('resource-metric-button lemma-index-tab', className)}
            data-active={active}
            aria-pressed={active}
            onClick={onClick}
        >
            <span className="font-medium">{label}</span>
            <span className="lemma-index-tab-count">{count}</span>
        </button>
    );
}

export function ResourceWorkSplit({
    main,
    aside,
    isStacked,
    isAsideFull,
    separator,
    asideClassName,
    className,
}: {
    main: ReactNode;
    aside?: ReactNode;
    isStacked?: boolean;
    isAsideFull?: boolean;
    separator?: ReactNode;
    asideClassName?: string;
    className?: string;
}) {
    return (
        <div
            className={cn(
                'flex h-full min-h-0 flex-1 overflow-hidden bg-transparent',
                isStacked && 'flex-col',
                className
            )}
        >
            {!isAsideFull ? <div className="min-h-0 min-w-0 flex-1">{main}</div> : null}
            {aside ? (
                <>
                    {!isStacked && !isAsideFull ? separator : null}
                    <aside
                        className={cn(
                            isAsideFull
                                ? 'min-h-0 min-w-0 flex-1 bg-[var(--bg-canvas)]'
                                : isStacked
                                    ? 'min-h-[320px] h-[42%] shrink-0 overflow-hidden border-t border-[color:color-mix(in_srgb,var(--border-subtle)_45%,transparent)] bg-[var(--bg-canvas)]'
                                    : 'h-full min-h-0 shrink-0 overflow-hidden border-l border-[color:color-mix(in_srgb,var(--border-subtle)_45%,transparent)] bg-[var(--bg-canvas)]',
                            asideClassName
                        )}
                    >
                        {aside}
                    </aside>
                </>
            ) : null}
        </div>
    );
}

export function ResourceList({
    children,
    className,
}: {
    children: ReactNode;
    className?: string;
}) {
    return <div className={cn('lemma-index-list', className)}>{children}</div>;
}

export function ResourceRow({
    children,
    className,
}: {
    children: ReactNode;
    className?: string;
}) {
    return <div className={cn('lemma-index-row', className)}>{children}</div>;
}
