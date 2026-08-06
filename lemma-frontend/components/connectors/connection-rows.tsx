'use client';

import type { ComponentType } from 'react';

import { Button } from '@/components/ui/button';
import { Database, Globe2, Pencil, Plus, RefreshCw, Wrench } from '@/components/ui/icons';
import { DestructiveResourceActionItem, ResourceActionsMenu } from '@/components/shared/resource-actions-menu';
import { DropdownMenuItem } from '@/components/ui/dropdown-menu';
import { StepLoader } from '@/components/brand/loader';
import { useConnectorOperations } from '@/lib/hooks/use-connectors';
import type { AuthConfig, Connector } from '@/lib/types';
import {
    KIND,
    describeInstallTarget,
    formatKindName,
    getAppLabel,
    getInstallLabel,
    getKindTagline,
    getTenantConfiguredKindSpec,
} from './connector-utils';

const KIND_ICONS: Record<string, ComponentType<{ className?: string }>> = {
    [KIND.SQL]: Database,
    [KIND.HTTP]: Globe2,
    [KIND.MCP]: Wrench,
};

/** Declared here rather than resolved inside a render, which remounts on every pass. */
function KindIcon({ kind, className }: { kind: string | null | undefined; className?: string }) {
    const Icon = KIND_ICONS[String(kind ?? '')] ?? Globe2;
    return <Icon className={className} />;
}

/**
 * The tile behind a kind's glyph.
 *
 * One tint across all three rather than one each: these are three doors onto the
 * same idea — a connection you configure — and the catalog's own rule is that a
 * screen of fallback tiles should read as one product, not a rainbow. The glyph
 * carries the difference. `connector-monogram` alone renders colourless: its
 * background and colour both resolve through `--connector-tint`, which only a
 * numbered tint class defines.
 */
function KindTile({ kind }: { kind: string | null | undefined }) {
    return (
        <span className="connector-monogram connector-monogram-8 flex h-8 w-8 shrink-0 items-center justify-center rounded-lg">
            <KindIcon kind={kind} className="h-4 w-4" />
        </span>
    );
}

/**
 * The entry point for connections the org configures itself.
 *
 * Above the catalog rather than inside it: you don't browse for a database the
 * way you browse for Slack, you arrive already knowing you have one. Rendered
 * from whichever catalog entries advertise a tenant-configured kind, so a
 * fourth one appears here without a code change.
 */
export function AddYourOwnRow({
    connectors,
    onAdd,
}: {
    connectors: Connector[];
    onAdd: (connector: Connector) => void;
}) {
    if (connectors.length === 0) return null;

    return (
        <section className="context-section">
            <div className="mb-3 flex items-center gap-2">
                <h2 className="text-base font-normal text-[var(--text-primary)]">Add your own</h2>
            </div>
            <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
                {connectors.map((connector) => {
                    const capability = getTenantConfiguredKindSpec(connector);
                    return (
                        // Card-shaped, but still a Button: these are three
                        // alternatives to each other and none of them is the
                        // action this screen exists for, which is what
                        // `secondary` means. The overrides are shape only —
                        // full width, two lines of text, content to the left.
                        <Button
                            key={connector.id}
                            type="button"
                            variant="secondary"
                            onClick={() => onAdd(connector)}
                            className="group h-auto w-full justify-start gap-3 rounded-lg p-3 text-left whitespace-normal"
                        >
                            <KindTile kind={capability?.kind} />
                            <span className="min-w-0 flex-1">
                                <span className="block truncate text-sm text-[var(--text-primary)]">
                                    {getAppLabel(connector)}
                                </span>
                                {/* No truncate: the tagline is short enough to sit on
                                    one line here and to wrap rather than vanish when
                                    the grid collapses to one column. */}
                                <span className="block text-xs leading-5 text-[var(--text-tertiary)]">
                                    {getKindTagline(String(capability?.kind ?? ''))}
                                </span>
                            </span>
                            <Plus className="h-4 w-4 shrink-0 text-[var(--text-tertiary)] transition-gentle group-hover:text-[var(--text-secondary)]" />
                        </Button>
                    );
                })}
            </div>
        </section>
    );
}

/**
 * One install of a tenant-configured connector.
 *
 * The target line is the point: two databases named alike are told apart by
 * `db.internal/analytics` and nothing else. The operation count is what says
 * whether discovery actually found anything — it runs server-side after the
 * install commits and swallows its failures, so an install with zero tools is
 * a normal state that needs a visible retry rather than a silent one.
 */
export function ConnectionRow({
    install,
    connector,
    organizationId,
    isBusy,
    onEdit,
    onRefresh,
    onDelete,
}: {
    install: AuthConfig;
    connector: Connector | null;
    organizationId?: string;
    isBusy: boolean;
    onEdit: (install: AuthConfig) => void;
    onRefresh: (install: AuthConfig) => void;
    onDelete: (install: AuthConfig) => void;
}) {
    const target = describeInstallTarget(install.kind, install.config);
    const { data: operations, isLoading: isLoadingOperations } = useConnectorOperations({
        organizationId,
        authConfigName: install.name,
    });
    const operationCount = operations?.length ?? 0;

    return (
        <div className="group flex items-center gap-3 rounded-lg px-3 py-2.5 transition-gentle hover:bg-[var(--surface-1)]">
            <KindTile kind={install.kind} />

            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-1.5">
                    <p className="truncate text-sm text-[var(--text-primary)]">
                        {getInstallLabel(install, connector)}
                    </p>
                    <span className="chip chip-sm chip-muted shrink-0">{formatKindName(install.kind)}</span>
                    {install.is_default ? (
                        <span className="chip chip-sm chip-muted shrink-0">Default</span>
                    ) : null}
                </div>
                <p className="truncate text-xs leading-5 text-[var(--text-tertiary)]">
                    {target ?? 'No address recorded'}
                    {isLoadingOperations
                        ? null
                        : ` · ${operationCount} operation${operationCount === 1 ? '' : 's'}`}
                </p>
            </div>

            {!isLoadingOperations && operationCount === 0 ? (
                <Button
                    variant="secondary"
                    size="sm"
                    className="h-8 shrink-0"
                    onClick={() => onRefresh(install)}
                    disabled={isBusy}
                >
                    {isBusy ? (
                        <StepLoader size="xs" className="mr-1.5" />
                    ) : (
                        <RefreshCw className="mr-1.5 h-3.5 w-3.5" />
                    )}
                    Retry discovery
                </Button>
            ) : null}

            <ResourceActionsMenu
                ariaLabel={`Open actions for ${getInstallLabel(install, connector)}`}
                triggerClassName="h-8 w-8 shrink-0 opacity-0 transition-opacity group-hover:opacity-100 group-focus-within:opacity-100"
            >
                <DropdownMenuItem
                    disabled={isBusy}
                    onSelect={(event) => {
                        event.preventDefault();
                        onEdit(install);
                    }}
                >
                    <Pencil className="mr-2 h-4 w-4" />
                    Edit connection
                </DropdownMenuItem>
                <DropdownMenuItem
                    disabled={isBusy}
                    onSelect={(event) => {
                        event.preventDefault();
                        onRefresh(install);
                    }}
                >
                    <RefreshCw className="mr-2 h-4 w-4" />
                    Refresh operations
                </DropdownMenuItem>
                <DestructiveResourceActionItem disabled={isBusy} onSelect={() => onDelete(install)}>
                    Delete connection
                </DestructiveResourceActionItem>
            </ResourceActionsMenu>
        </div>
    );
}
