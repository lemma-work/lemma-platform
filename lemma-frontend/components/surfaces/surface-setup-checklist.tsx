'use client';

import { useState } from 'react';
import { Check, Copy, ExternalLink, ShieldCheck } from '@/components/ui/icons';
import { toast } from 'sonner';

import { playSoundFeedback } from '@/lib/feedback/sound-feedback';
import type { SurfaceSetupAction, SurfaceSetupActionField } from 'lemma-sdk';

/**
 * The steps Lemma genuinely cannot do for you — pasting a callback URL into
 * Meta, pointing a Slack app's events at us, getting a tenant admin to approve.
 *
 * The backend only emits these when the user really has to act, so anything
 * rendered here is a real blocker. It shows *during* setup rather than after,
 * which is the whole reason it is a component instead of a section of the old
 * config dialog.
 */
export function SurfaceSetupChecklist({
    actions,
    consentUrl,
}: {
    actions: SurfaceSetupAction[];
    consentUrl?: string | null;
}) {
    if (!actions.length && !consentUrl) return null;

    return (
        <div className="grid gap-3">
            {consentUrl ? (
                <a
                    href={consentUrl}
                    target="_blank"
                    rel="noreferrer"
                    className="surface-inline-callout flex items-center justify-between gap-2 text-sm text-[var(--text-primary)] hover:underline"
                >
                    <span className="flex items-center gap-2">
                        <ShieldCheck className="h-4 w-4" /> Grant admin consent
                    </span>
                    <ExternalLink className="h-3.5 w-3.5" />
                </a>
            ) : null}
            {actions.map((action) => (
                <SetupActionCard key={action.key} action={action} />
            ))}
        </div>
    );
}

function SetupActionCard({ action }: { action: SurfaceSetupAction }) {
    const fields = action.fields ?? [];
    const steps = action.steps ?? [];

    return (
        <div className="surface-panel-muted grid gap-3 p-3">
            <div>
                <p className="text-sm font-medium text-[var(--text-primary)]">{action.title}</p>
                {action.description ? (
                    <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">{action.description}</p>
                ) : null}
            </div>

            {fields.length ? (
                <div className="grid gap-2">
                    {fields.map((field, index) => (
                        <SetupCopyField key={`${field.label}-${index}`} field={field} />
                    ))}
                </div>
            ) : null}

            {steps.length ? (
                <ol className="grid gap-1.5">
                    {steps.map((step, index) => (
                        <li key={index} className="flex gap-2 text-xs leading-5 text-[var(--text-secondary)]">
                            <span className="surface-step-number">{index + 1}</span>
                            <span className="min-w-0">{step}</span>
                        </li>
                    ))}
                </ol>
            ) : null}

            {action.link ? (
                <a
                    href={action.link}
                    target="_blank"
                    rel="noreferrer"
                    className="inline-flex w-fit items-center gap-1.5 text-xs font-medium text-[var(--action-primary)] hover:underline"
                >
                    {action.link_label || 'Open dashboard'} <ExternalLink className="h-3.5 w-3.5" />
                </a>
            ) : null}
        </div>
    );
}

/** A value the user pastes elsewhere. Secrets stay masked until revealed, so a
 * shared screen doesn't leak a verify token. */
export function SetupCopyField({ field }: { field: SurfaceSetupActionField }) {
    const [copied, setCopied] = useState(false);
    const [revealed, setRevealed] = useState(false);
    const masked = Boolean(field.secret) && !revealed;

    const copy = async () => {
        try {
            await navigator.clipboard.writeText(field.value);
            setCopied(true);
            playSoundFeedback('action-success');
            setTimeout(() => setCopied(false), 1500);
        } catch {
            toast.error('Could not copy to clipboard');
        }
    };

    return (
        <div className="grid gap-1">
            <div className="flex items-center justify-between gap-2">
                <span className="type-eyebrow-medium">{field.label}</span>
                {field.secret ? (
                    <button
                        type="button"
                        onClick={() => setRevealed((current) => !current)}
                        className="lemma-quiet-text-button custom-focus-ring rounded text-xs text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]"
                    >
                        {revealed ? 'Hide' : 'Reveal'}
                    </button>
                ) : null}
            </div>
            <button
                type="button"
                onClick={() => void copy()}
                className="surface-copy-field custom-focus-ring"
                aria-label={`Copy ${field.label}`}
            >
                <span className="min-w-0 break-all font-mono text-xs text-[var(--text-primary)]">
                    {masked ? '•'.repeat(Math.min(field.value.length, 24)) : field.value}
                </span>
                {copied ? (
                    <Check className="h-3.5 w-3.5 shrink-0 text-[var(--state-success)]" />
                ) : (
                    <Copy className="h-3.5 w-3.5 shrink-0 text-[var(--text-tertiary)]" />
                )}
            </button>
        </div>
    );
}
