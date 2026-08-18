'use client';

import Link from 'next/link';
import { ExternalLink } from '@/components/ui/icons';

import { CreateSlackAppButton } from '@/components/connectors/create-slack-app-button';
import { SchemaFields } from '@/components/connectors/schema-fields';
import { Button } from '@/components/ui/button';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select';
import { credentialSchema, type CatalogSurface } from '@/lib/surfaces/catalog';
import type { SurfacePlatformDefinition } from '@/lib/surfaces/registry';
import type { Account } from '@/lib/types';
import { cn } from '@/lib/utils';

export type CredentialValues = Record<string, unknown>;

/**
 * Everything a bring-your-own surface needs before it can be created.
 *
 * Two shapes, one step: pick an account that already exists (OAuth platforms,
 * where connecting happens elsewhere), or walk the platform's journey and type
 * the credentials inline (token platforms). The journey's steps and its input
 * are interleaved — the field lives *inside* the step that produces it, rather
 * than under a block of instructions the user has to hold in their head.
 */
export function SurfaceConnectStep({
    definition,
    catalog,
    accounts,
    accountId,
    onAccountChange,
    credentials,
    onCredentialsChange,
    podId,
}: {
    definition: SurfacePlatformDefinition;
    catalog: CatalogSurface | null;
    accounts: Account[];
    accountId: string;
    onAccountChange: (accountId: string) => void;
    credentials: CredentialValues;
    onCredentialsChange: (values: CredentialValues) => void;
    podId: string;
}) {
    const schema = credentialSchema(catalog);
    const journey = definition.journey;

    if (journey && schema) {
        return (
            <div className="grid gap-4">
                <p className="text-sm text-[var(--text-secondary)]">{journey.title}</p>

                <ol className="surface-journey">
                    {journey.steps.map((step, index) => (
                        <li key={index} className={cn('surface-journey-step', step.optional && 'is-optional')}>
                            <span className="surface-step-number">
                                {step.optional ? '·' : index + 1}
                            </span>
                            <div className="min-w-0 flex-1">
                                <div className="flex flex-wrap items-center gap-2">
                                    <span className="text-sm leading-6 text-[var(--text-secondary)]">{step.text}</span>
                                    {step.link ? (
                                        <a
                                            href={step.link}
                                            target="_blank"
                                            rel="noreferrer"
                                            className="inline-flex items-center gap-1 text-xs font-medium text-[var(--action-primary)] hover:underline"
                                        >
                                            {step.linkLabel || 'Open'} <ExternalLink className="h-3 w-3" />
                                        </a>
                                    ) : null}
                                </div>

                                {step.field ? (
                                    <div className="mt-2">
                                        <SchemaFields
                                            schema={schema}
                                            values={credentials}
                                            onChange={onCredentialsChange}
                                            emptyMessage="No credentials are required."
                                            autoFocusFirst
                                        />
                                    </div>
                                ) : null}
                            </div>
                        </li>
                    ))}
                </ol>

                {/* A journey whose credential step never declared a field would
                    silently render no input — fall back to the whole form. */}
                {journey.steps.every((step) => !step.field) ? (
                    <SchemaFields
                        schema={schema}
                        values={credentials}
                        onChange={onCredentialsChange}
                        emptyMessage="No credentials are required."
                    />
                ) : null}
            </div>
        );
    }

    return (
        <div className="grid gap-3">
            <p className="text-sm text-[var(--text-secondary)]">
                Which {definition.accountLabel.toLowerCase()} should this run on?
            </p>
            {accounts.length > 0 ? (
                <Select value={accountId} onValueChange={onAccountChange}>
                    <SelectTrigger className="h-10 bg-[var(--field-bg)]">
                        <SelectValue placeholder={`Select ${definition.accountLabel.toLowerCase()}`} />
                    </SelectTrigger>
                    <SelectContent>
                        {accounts.map((account) => (
                            <SelectItem key={account.id} value={account.id}>
                                {account.display_name || account.email || account.connector?.title || account.id}
                            </SelectItem>
                        ))}
                    </SelectContent>
                </Select>
            ) : (
                <div className="surface-inline-callout">
                    <p className="text-sm text-[var(--text-primary)]">
                        No {definition.label} account connected yet
                    </p>
                    <p className="mt-1 text-xs leading-5 text-[var(--text-secondary)]">
                        Signing in to {definition.label} happens once for the whole organization.
                        Do that first, then come back here.
                    </p>
                    <Button asChild className="mt-3" size="sm" variant="secondary">
                        <Link href={`/pod/${podId}/connectors`}>Open connectors</Link>
                    </Button>
                    {/* Running your own Slack app is a second route to the same
                        place, not a footnote on this one — and it starts here,
                        because making the app is what produces the credentials
                        connectors then asks for. Burying it behind the link
                        above meant nobody found it: it sat inside the custom
                        credential form, on a row action that hid itself once
                        Slack was connected. */}
                    {definition.platform === 'SLACK' ? (
                        <div className="mt-4 border-t border-[var(--border-subtle)] pt-3">
                            <p className="mb-2 text-xs leading-5 text-[var(--text-secondary)]">
                                Or run Lemma under your own name in Slack — your workspace,
                                your app, your bot’s name and icon.
                            </p>
                            <CreateSlackAppButton />
                        </div>
                    ) : null}
                </div>
            )}
        </div>
    );
}
