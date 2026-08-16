'use client';

import Image from 'next/image';
import type { ReactNode } from 'react';

import { RefreshCw, TerminalSquare } from '@/components/ui/icons';
import { Button } from '@/components/ui/button';
import { cn } from '@/lib/utils';
import {
    agentHostHarnessHealth,
    agentHostHarnessModelCount,
    harnessLogo,
} from './agent-runtime-helpers';

/** Just enough of a harness to describe it; both callers pass the wire shape. */
export type HarnessRowHarness = {
    harness_key: string;
    display_name: string;
    adapter_version: string;
    upstream_version?: string | null;
    health: string;
    stale_reason?: string | null;
    config_options?: Array<{ category: string; options?: Array<Record<string, unknown>> }> | null;
};

/** The profile this harness already has, if any. Narrow on purpose: onboarding
 *  builds this from the management listing and never holds the full profile. */
export type HarnessRowProfile = { name: string; archived: boolean };

/**
 * The small state pill used across the agents settings — harnesses, computers,
 * providers and saved profiles all wear it, so it lives beside the row that
 * needed extracting rather than being duplicated on either side of the split.
 */
export function StatusBadge({ label, tone }: { label: string; tone: 'ok' | 'muted' }) {
    return (
        <span
            className={cn(
                'shrink-0 rounded-full px-2 py-0.5 text-xs font-medium',
                tone === 'ok'
                    ? 'bg-[var(--state-success-soft,var(--surface-1))] text-[var(--state-success,var(--text-secondary))]'
                    : 'bg-[var(--surface-1)] text-[var(--text-tertiary)]',
            )}
        >
            {label}
        </span>
    );
}

/**
 * One coding agent found on a computer, described the same way everywhere.
 *
 * Extracted because the two places that list harnesses disagreed about how much
 * to say. Manage models rendered health, the reason behind it, the agent's own
 * logo and its model count, and withheld "Add" from anything not usable.
 * Onboarding — where a new user actually meets this list — rendered the display
 * name and an Add button, and nothing else. So a Claude Code that was installed
 * but signed out looked exactly like a working one, was offered for adoption,
 * and failed on save; or worse, saved, and failed in the first chat instead.
 *
 * Everything about *what this harness is* lives here. What a caller may do with
 * it does not: the action slot is theirs, because "Add to models" and "Use in
 * chats" are genuinely different offers made in different places.
 */
export function HarnessRow({
    harness,
    hostOnline = true,
    savedProfile,
    action,
    onRecheck,
    className,
}: {
    harness: HarnessRowHarness;
    /** A healthy harness on an unreachable computer still cannot take work. */
    hostOnline?: boolean;
    savedProfile?: HarnessRowProfile | null;
    /** Rendered when the harness is usable and not already added. */
    action?: (usable: boolean) => ReactNode;
    /** Offered on a harness that needs signing in, where it is the actual fix. */
    onRecheck?: () => void;
    className?: string;
}) {
    const health = agentHostHarnessHealth(harness.health);
    const modelCount = agentHostHarnessModelCount(harness.config_options ?? []);
    // The agent's version and how many models it offers — the two things about a
    // row that are the user's business.
    //
    // The adapter version led this line and is not: it is the ACP bridge Lemma
    // pins and installs, so it is the same for everyone on a release and can only
    // ever be a number they cannot act on. It was also the *only* thing an
    // installing row could show, which is how a row mid-install came to read
    // "adapter 0.62.0" over two identical sentences about installing. It is still
    // in the log and the API for support; it is not a fact about their computer.
    const facts = [
        harness.upstream_version ? `agent ${harness.upstream_version}` : null,
        modelCount ? `${modelCount} model${modelCount === 1 ? '' : 's'}` : null,
    ].filter((fact): fact is string => fact !== null);
    const logo = harnessLogo(harness.harness_key);
    const usable = health.ready && hostOnline;
    const blockedReason = usable ? null : hostOnline ? health.detail : 'That computer is not reachable right now.';

    return (
        <div className={cn('rounded-md bg-[var(--surface-1)] px-3 py-3', className)}>
            <div className="flex flex-wrap items-center gap-3">
                <span className="flex size-7 shrink-0 items-center justify-center rounded-md bg-[var(--surface-2)]">
                    {logo ? (
                        <Image src={logo} alt="" width={16} height={16} className="size-4 object-contain" />
                    ) : (
                        <TerminalSquare className="size-3.5 text-[var(--text-tertiary)]" />
                    )}
                </span>
                <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium text-[var(--text-primary)]">
                        {harness.display_name}
                    </div>
                    {/*
                      * Rendered only when there is something to say. A row that
                      * is still setting up has neither a version nor a model
                      * count yet, and the empty div left a blank line under the
                      * name for the whole of the wait.
                      */}
                    {facts.length > 0 ? (
                        <div className="text-xs text-[var(--text-tertiary)]">
                            {facts.join(' · ')}
                        </div>
                    ) : null}
                </div>
                {savedProfile ? (
                    <StatusBadge
                        label={
                            savedProfile.archived
                                ? `Archived as ${savedProfile.name}`
                                : `Added as ${savedProfile.name}`
                        }
                        tone="muted"
                    />
                ) : null}
                <StatusBadge label={health.label} tone={usable ? 'ok' : 'muted'} />
            </div>
            {blockedReason ? <p className="mt-2 text-xs text-[var(--text-tertiary)]">{blockedReason}</p> : null}
            {action ? <div className="mt-2">{action(usable)}</div> : null}
            {/*
              * The copy for AUTH_REQUIRED already says "then let Agent Host
              * re-probe", and until now there was nothing anywhere to press.
              * Probing is otherwise on a fifteen-minute timer, so someone who
              * signed in did so and then watched nothing happen.
              */}
            {onRecheck && harness.health === 'AUTH_REQUIRED' ? (
                <div className="mt-2">
                    <Button
                        type="button"
                        size="sm"
                        variant="quiet"
                        className="gap-1.5 px-2"
                        onClick={onRecheck}
                    >
                        <RefreshCw className="size-3.5" />
                        I&apos;ve signed in — re-check
                    </Button>
                </div>
            ) : null}
            {harness.stale_reason ? (
                <p className="mt-1 text-xs text-[var(--text-tertiary)]">{harness.stale_reason}</p>
            ) : null}
        </div>
    );
}
