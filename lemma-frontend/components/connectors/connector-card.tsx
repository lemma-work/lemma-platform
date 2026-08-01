'use client';

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { Check, ExternalLink, RefreshCw } from '@/components/ui/icons';
import type { Account, Connector } from '@/lib/types';
import { ConnectorIcon } from './connector-icon';
import {
    getAccountStatusMeta,
    getPrimaryCapability,
    usesDirectCredentials,
} from './connector-utils';
import { StepLoader } from '@/components/brand/loader';

/**
 * A catalog entry. Rows rather than cards: the catalog is something you scan for
 * a name you already have in mind, and 79 equal-weight cards with a full-width
 * button each reads as a table of records, not a set of apps.
 *
 * Connected state lives next to the name, which leaves the action zone holding
 * exactly one control at a fixed width — that's what keeps the buttons on a
 * common right edge across both columns.
 */
export function ConnectorRow({
    app,
    isConnected,
    isBusy,
    hasAdvanced,
    onConnect,
    onAdvanced,
}: {
    app: Connector;
    isConnected: boolean;
    isBusy: boolean;
    hasAdvanced: boolean;
    onConnect: (app: Connector) => void;
    onAdvanced: (app: Connector) => void;
}) {
    const capability = getPrimaryCapability(app);
    const connectsWithCredentials = usesDirectCredentials(capability);
    const label = app.title || app.name || app.id;

    return (
        <div className="group flex items-center gap-3 rounded-lg px-3 py-2.5 transition-gentle hover:bg-[var(--surface-1)]">
            <ConnectorIcon connectorId={app.id} icon={app.icon} label={label} size="sm" />

            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                    <p className="truncate text-sm text-[var(--text-primary)]">{label}</p>
                    {isConnected ? (
                        <Check
                            aria-label="Connected"
                            className="h-3.5 w-3.5 shrink-0 text-[var(--state-success)]"
                        />
                    ) : null}
                </div>
                {app.description ? (
                    <p className="truncate text-xs leading-5 text-[var(--text-tertiary)]">{app.description}</p>
                ) : null}
            </div>

            {hasAdvanced && !isConnected ? (
                <Button
                    variant="quiet"
                    size="sm"
                    className="h-8 shrink-0 px-2 text-xs text-[var(--text-tertiary)] opacity-0 transition-gentle group-hover:opacity-100 group-focus-within:opacity-100"
                    onClick={() => onAdvanced(app)}
                    disabled={isBusy}
                >
                    Advanced
                </Button>
            ) : null}

            <div className="flex w-[124px] shrink-0 justify-end">
                <Button
                    variant={isConnected ? 'quiet' : 'secondary'}
                    size="sm"
                    className="h-8"
                    onClick={() => onConnect(app)}
                    disabled={isBusy}
                >
                    {isBusy ? (
                        <>
                            <StepLoader size="xs" className="mr-1.5" />
                            Connecting
                        </>
                    ) : (
                        <>
                            {isConnected ? 'Add another' : 'Connect'}
                            {connectsWithCredentials ? null : <ExternalLink className="ml-1.5 h-3.5 w-3.5" />}
                        </>
                    )}
                </Button>
            </div>
        </div>
    );
}

/**
 * A connected account. Same row rhythm as the catalog below it — six accounts
 * are six one-line facts, and giving them full cards made the top of the page
 * read at a completely different density from the rest.
 */
export function ConnectedAccountRow({
    account,
    isBusy,
    onReconnect,
    onDisconnect,
}: {
    account: Account;
    isBusy: boolean;
    onReconnect: (account: Account) => void;
    onDisconnect: (account: Account) => void;
}) {
    const status = getAccountStatusMeta(account.status);
    const appName = account.connector?.title || account.connector?.name || 'Unknown app';
    // Sitting under "Your accounts" already says connected — only the exceptions
    // earn a status badge, so a healthy account reads as a name and nothing else.
    const subtitle = account.display_name || account.email;

    return (
        <div className="group flex items-center gap-3 rounded-lg px-3 py-2.5 transition-gentle hover:bg-[var(--surface-1)]">
            <ConnectorIcon
                connectorId={account.connector_id}
                icon={account.connector?.icon}
                label={appName}
                size="sm"
            />

            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                    <p className="truncate text-sm text-[var(--text-primary)]">{appName}</p>
                    {account.is_default ? (
                        <span className="chip chip-sm chip-muted shrink-0">Default</span>
                    ) : null}
                </div>
                {subtitle ? (
                    <p className="truncate text-xs leading-5 text-[var(--text-tertiary)]">{subtitle}</p>
                ) : null}
            </div>

            {status.needsAttention ? (
                <>
                    <Badge variant={status.variant} title={status.hint} className="shrink-0">
                        {status.label}
                    </Badge>
                    <Button
                        variant="secondary"
                        size="sm"
                        className="h-8 shrink-0"
                        onClick={() => onReconnect(account)}
                        disabled={isBusy}
                    >
                        {isBusy ? (
                            <StepLoader size="xs" className="mr-1.5" />
                        ) : (
                            <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                        )}
                        Reconnect
                    </Button>
                </>
            ) : null}

            <ResourceActionsMenu
                ariaLabel={`Open actions for ${appName}`}
                triggerClassName="h-8 w-8 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
            >
                <DestructiveResourceActionItem disabled={isBusy} onSelect={() => onDisconnect(account)}>
                    Disconnect
                </DestructiveResourceActionItem>
            </ResourceActionsMenu>
        </div>
    );
}
