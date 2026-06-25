'use client';

import Link from 'next/link';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';
import { AlertCircle, CheckCircle2, X } from 'lucide-react';
import { toast } from 'sonner';
import { ApiError } from 'lemma-sdk';

import { cn } from '@/lib/utils';

type ResourceFeedbackTone = 'success' | 'error' | 'warning' | 'info';

export type ResourceFeedbackAction = {
    label: string;
    href?: string;
    onClick?: () => void;
    variant?: 'primary' | 'secondary' | 'ghost' | 'outline';
};

function formatErrorDetails(details: unknown): string | null {
    if (!Array.isArray(details) || details.length === 0) return null;
    const MAX = 4;
    const parts: string[] = [];
    for (const entry of details) {
        if (!entry || typeof entry !== 'object') continue;
        const record = entry as Record<string, unknown>;
        const msg = typeof record.msg === 'string' ? record.msg.trim() : '';
        if (!msg) continue;
        const loc = Array.isArray(record.loc) ? record.loc.filter((s): s is string => typeof s === 'string') : null;
        const field = loc && loc.length > 0 ? loc.filter((segment) => segment !== 'body').join('.') : '';
        parts.push(field ? `${field}: ${msg}` : msg);
    }
    if (parts.length === 0) return null;
    const shown = parts.slice(0, MAX);
    const remainder = parts.length - shown.length;
    let text = shown.join('; ');
    if (remainder > 0) text += `; +${remainder} more`;
    return text;
}

export function getResourceErrorMessage(error: unknown, fallback: string) {
    if (error instanceof ApiError) {
        const base = error.message?.trim() || fallback;
        const detailsText = formatErrorDetails(error.details);
        if (detailsText) return `${base} — ${detailsText}`;
        return base || fallback;
    }
    if (error instanceof Error && error.message.trim()) return error.message;
    if (typeof error === 'string' && error.trim()) return error;
    return fallback;
}

export function showResourceCreatedToast(resourceLabel: string, resourceName?: string | null) {
    toast.success(`${resourceLabel} created`, {
        description: resourceName ? resourceName : undefined,
    });
}

export function showResourceErrorToast(error: unknown, fallback: string) {
    toast.error(fallback, {
        description: getResourceErrorMessage(error, fallback),
    });
}

export function ResourceFeedbackBanner({
    tone = 'success',
    title,
    description,
    actions,
    onDismiss,
    celebrate = false,
    className,
}: {
    tone?: ResourceFeedbackTone;
    title: string;
    description?: string;
    actions?: ResourceFeedbackAction[];
    onDismiss?: () => void;
    celebrate?: boolean;
    className?: string;
}) {
    const isSuccess = tone === 'success';
    const isError = tone === 'error';
    const toneClass = {
        success: 'surface-panel-muted border-tone-success',
        error: 'state-surface-error',
        warning: 'state-surface-warning',
        info: 'surface-panel-muted border-tone-info',
    }[tone];

    return (
        <section
            role={isError ? 'alert' : 'status'}
            className={cn(
                'relative overflow-hidden px-3 py-2.5',
                toneClass,
                className
            )}
        >
            {celebrate && isSuccess ? <ConfettiBurst density="small" /> : null}
            <div className="relative z-10 flex items-start gap-2.5">
                <div className={cn(
                    'mt-0.5 flex h-6 w-6 shrink-0 items-center justify-center rounded-md',
                    isError ? 'bg-[var(--card-bg)] text-[var(--state-error)]' : 'bg-[var(--card-bg)] text-[var(--state-success)]'
                )}>
                    {isError ? <AlertCircle className="h-4 w-4" /> : <CheckCircle2 className="h-4 w-4" />}
                </div>
                <div className="min-w-0 flex-1">
                    <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                        <h2 className="text-sm font-semibold text-[var(--text-primary)]">{title}</h2>
                        {description ? (
                            <p className="min-w-[12rem] flex-1 truncate text-sm text-[var(--text-secondary)]">{description}</p>
                        ) : null}
                    </div>
                    {actions?.length ? (
                        <div className="mt-2 flex flex-wrap gap-1.5">
                            {actions.map((action) => (
                                action.href ? (
                                    <Link
                                        key={`${action.label}-${action.href}`}
                                        href={action.href}
                                        className={cn(
                                            'custom-focus-ring lemma-quiet-action lemma-quiet-action-sm border border-[var(--button-secondary-border)] bg-[var(--button-secondary-bg)] px-2.5',
                                            action.variant === 'primary' && 'text-[var(--action-primary)]'
                                        )}
                                    >
                                        {action.label}
                                    </Link>
                                ) : (
                                    <button
                                        key={action.label}
                                        type="button"
                                        onClick={action.onClick}
                                        className={cn(
                                            'resource-feedback-action-button custom-focus-ring lemma-quiet-action lemma-quiet-action-sm border border-[var(--button-secondary-border)] bg-[var(--button-secondary-bg)] px-2.5',
                                            action.variant === 'primary' && 'text-[var(--action-primary)]'
                                        )}
                                    >
                                        {action.label}
                                    </button>
                                )
                            ))}
                        </div>
                    ) : null}
                </div>
                {onDismiss ? (
                    <button
                        type="button"
                        aria-label="Dismiss"
                        title="Dismiss"
                        onClick={onDismiss}
                        className="lemma-quiet-icon-button custom-focus-ring h-6 w-6 shrink-0 text-[var(--text-tertiary)]"
                    >
                        <X className="h-3 w-3" />
                    </button>
                ) : null}
            </div>
        </section>
    );
}

export function ResourceArrivalNotice({
    resource,
    title,
    description,
    actions,
    celebrate = false,
    className,
}: {
    resource: string;
    title: string;
    description?: string;
    actions?: ResourceFeedbackAction[];
    celebrate?: boolean;
    className?: string;
}) {
    const router = useRouter();
    const pathname = usePathname();
    const searchParams = useSearchParams();
    const created = searchParams.get('created');

    if (created !== resource) return null;

    const dismiss = () => {
        const nextParams = new URLSearchParams(searchParams.toString());
        nextParams.delete('created');
        const nextQuery = nextParams.toString();
        router.replace(nextQuery ? `${pathname}?${nextQuery}` : pathname, { scroll: false });
    };

    return (
        <ResourceFeedbackBanner
            tone="success"
            title={title}
            description={description}
            actions={actions}
            onDismiss={dismiss}
            celebrate={celebrate}
            className={className}
        />
    );
}

function ConfettiBurst({ density = 'full' }: { density?: 'full' | 'small' }) {
    const count = density === 'small' ? 18 : 42;

    return (
        <div className="pointer-events-none absolute inset-0 z-0 overflow-hidden" aria-hidden="true">
            {Array.from({ length: count }, (_, index) => (
                <span
                    key={index}
                    className={cn(
                        'run-confetti-piece',
                        `run-confetti-piece-${index % 18}`,
                        `run-confetti-color-${index % 4}`,
                        index % 3 === 0 && 'run-confetti-wide',
                        index % 4 === 0 && 'run-confetti-tall'
                    )}
                />
            ))}
        </div>
    );
}
