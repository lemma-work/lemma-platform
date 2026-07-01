'use client';

import { Button } from '@/components/ui/button';
import { Badge } from '@/components/ui/badge';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { CheckCircle2, ExternalLink, Loader2, Plug, RefreshCw } from 'lucide-react';
import Image from 'next/image';
import type { Account, Connector } from '@/lib/types';
import {
    getAccountStatusMeta,
    getAppLabel,
    getPrimaryCapability,
    usesDirectCredentials,
} from './connector-utils';

function ConnectorIcon({ icon, alt }: { icon?: string | null; alt: string }) {
    if (icon) {
        return (
            <div className="relative h-10 w-10 shrink-0 rounded-lg bg-transparent p-1.5">
                <Image src={icon} alt={alt} fill className="object-contain p-1" />
            </div>
        );
    }
    return (
        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-[color:color-mix(in_srgb,var(--surface-2)_46%,transparent)]">
            <Plug className="h-5 w-5 text-[var(--text-tertiary)]" />
        </div>
    );
}

export function ConnectorCard({
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

    return (
        <div className="resource-index-card group p-4">
            <div className="mb-3 flex items-start justify-between gap-3">
                <div className="flex items-center gap-3">
                    <ConnectorIcon icon={app.icon} alt={getAppLabel(app)} />
                    <div>
                        <p className="text-sm font-normal text-[var(--text-primary)]">{app.title || app.name}</p>
                    </div>
                </div>
                {isConnected ? (
                    <span className="inline-flex shrink-0 items-center gap-1.5 py-1 text-xs font-medium text-[var(--state-success)]">
                        <CheckCircle2 className="h-3.5 w-3.5" />
                        Connected
                    </span>
                ) : null}
            </div>

            <p className="mb-4 min-h-[44px] line-clamp-2 text-sm leading-6 text-[var(--text-secondary)]">
                {app.description || `Connect ${getAppLabel(app)} to your workflows.`}
            </p>

            <div className="flex items-center gap-2">
                <Button
                    className="flex-1 justify-center"
                    variant={isConnected ? 'outline' : 'primary'}
                    onClick={() => onConnect(app)}
                    disabled={isBusy || isConnected}
                >
                    {isBusy ? (
                        <>
                            <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                            Connecting...
                        </>
                    ) : isConnected ? (
                        'Connected'
                    ) : (
                        <>
                            Connect
                            {connectsWithCredentials ? null : <ExternalLink className="ml-2 h-4 w-4" />}
                        </>
                    )}
                </Button>
                {hasAdvanced && !isConnected ? (
                    <Button
                        variant="ghost"
                        size="sm"
                        className="h-9 px-2 text-xs text-[var(--text-tertiary)]"
                        onClick={() => onAdvanced(app)}
                        disabled={isBusy}
                    >
                        Advanced
                    </Button>
                ) : null}
            </div>
        </div>
    );
}

export function ConnectedAccountCard({
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

    return (
        <div className="resource-index-card group p-4">
            <div className="flex items-start justify-between gap-3">
                <div className="flex min-w-0 items-center gap-3">
                    <ConnectorIcon icon={account.connector?.icon} alt={appName} />
                    <div className="min-w-0">
                        <p className="truncate text-sm font-normal text-[var(--text-primary)]">{appName}</p>
                        <p className="truncate text-xs text-[var(--text-tertiary)]">{account.email || 'Connected'}</p>
                    </div>
                </div>
                <ResourceActionsMenu
                    ariaLabel={`Open actions for ${appName}`}
                    triggerClassName="h-8 w-8 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
                >
                    <DestructiveResourceActionItem disabled={isBusy} onSelect={() => onDisconnect(account)}>
                        Disconnect
                    </DestructiveResourceActionItem>
                </ResourceActionsMenu>
            </div>

            <div className="mt-3 flex items-center justify-between gap-2">
                <Badge variant={status.variant} title={status.hint}>
                    {status.label}
                </Badge>
                {status.needsAttention ? (
                    <Button
                        variant="outline"
                        size="sm"
                        className="h-8"
                        onClick={() => onReconnect(account)}
                        disabled={isBusy}
                    >
                        {isBusy ? (
                            <Loader2 className="mr-2 h-3.5 w-3.5 animate-spin" />
                        ) : (
                            <RefreshCw className="mr-2 h-3.5 w-3.5" />
                        )}
                        Reconnect
                    </Button>
                ) : null}
            </div>
        </div>
    );
}
