'use client';

import { useEffect, useMemo, useState } from 'react';
import { Button } from '@/components/ui/button';
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { AlertTriangle } from '@/components/ui/icons';
import { buildSchemaFormPayload, buildSchemaFormValues } from 'lemma-sdk';
import type { AuthConfig, Connector } from '@/lib/types';
import { SchemaFields } from './schema-fields';
import { StepLoader } from '@/components/brand/loader';
import {
    getAppLabel,
    getConfigSchema,
    getCredentialSchema,
    getKindDescription,
    getTenantConfiguredKindSpec,
    schemaHasFields,
    type ConnectorKindSpec,
    type SchemaValues,
} from './connector-utils';

export interface ConnectionTarget {
    connector: Connector;
    /** Present when editing an install rather than adding one. */
    install?: AuthConfig | null;
}

export interface ConnectionSubmission {
    name: string;
    config: Record<string, unknown>;
    credentials: Record<string, unknown>;
}

/**
 * Adding a database, an API or an MCP server.
 *
 * One dialog rather than the enable-then-connect pair the other kinds use. That
 * split is a backend distinction — an install holds the address, an account
 * holds the credentials — and for these kinds it has no meaning to a person:
 * you type a host and a password in one sitting. Splitting it is also what let
 * the old flow ask for a database password without ever asking which database.
 *
 * Editing reuses the same form minus the credentials, which belong to the
 * account and are rotated by reconnecting it.
 */
export function AddConnectionDialog({
    target,
    isSubmitting,
    existingNames,
    error,
    onOpenChange,
    onSubmit,
}: {
    target: ConnectionTarget | null;
    isSubmitting: boolean;
    /** Active install names in the org — unique per org, so collisions are caught here. */
    existingNames: string[];
    error: string | null;
    onOpenChange: (open: boolean) => void;
    onSubmit: (submission: ConnectionSubmission) => void;
}) {
    const connector = target?.connector ?? null;
    const install = target?.install ?? null;
    const isEdit = Boolean(install);

    const capability = useMemo<ConnectorKindSpec | null>(
        () => getTenantConfiguredKindSpec(connector),
        [connector],
    );
    const configSchema = getConfigSchema(capability);
    const credentialSchema = getCredentialSchema(capability);
    // In edit mode the credential section is absent: an install's accounts keep
    // their own credentials, and the backend flags them for reconnect if the
    // config change invalidated them.
    const showCredentials = !isEdit && schemaHasFields(credentialSchema);

    const [name, setName] = useState('');
    const [config, setConfig] = useState<SchemaValues>({});
    const [credentials, setCredentials] = useState<SchemaValues>({});
    const [localError, setLocalError] = useState<string | null>(null);

    useEffect(() => {
        if (!target) return;
        setName(install?.name && install.name !== install.connector_id ? install.name : '');
        setConfig(buildSchemaFormValues(configSchema, (install?.config ?? {}) as SchemaValues));
        setCredentials(buildSchemaFormValues(credentialSchema));
        setLocalError(null);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [target?.connector.id, target?.install?.id]);

    const trimmedName = name.trim();
    const takenNames = useMemo(
        () => new Set(existingNames.filter((existing) => existing !== install?.name)),
        [existingNames, install?.name],
    );
    const nameIsTaken = trimmedName.length > 0 && takenNames.has(trimmedName);

    const handleSubmit = () => {
        if (!connector) return;
        if (!trimmedName) {
            setLocalError('Give this connection a name — it is how you tell it apart later.');
            return;
        }
        if (nameIsTaken) {
            setLocalError(`"${trimmedName}" is already used by another connection.`);
            return;
        }

        const configPayload = buildSchemaFormPayload(configSchema, config);
        if (!configPayload.isValid) {
            setLocalError(Object.values(configPayload.errors)[0] || 'Connection details are incomplete');
            return;
        }
        const credentialPayload = showCredentials
            ? buildSchemaFormPayload(credentialSchema, credentials)
            : { data: {}, errors: {}, isValid: true };
        if (!credentialPayload.isValid) {
            setLocalError(Object.values(credentialPayload.errors)[0] || 'Credentials are incomplete');
            return;
        }

        setLocalError(null);
        onSubmit({
            name: trimmedName,
            config: configPayload.data,
            credentials: credentialPayload.data,
        });
    };

    const label = getAppLabel(connector);
    const shownError = localError ?? error;

    return (
        <Dialog open={Boolean(target)} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>{isEdit ? `Edit ${label}` : `Add ${label}`}</DialogTitle>
                    <DialogDescription>
                        {isEdit
                            ? 'Change where this connection points. Accounts stay attached; any whose credentials this invalidates will ask to reconnect.'
                            : getKindDescription(String(capability?.kind ?? ''), capability)}
                    </DialogDescription>
                </DialogHeader>

                <div className="space-y-4 py-2">
                    <div className="space-y-1.5">
                        <Label htmlFor="connection-name">Name *</Label>
                        <Input
                            id="connection-name"
                            name="connection-name"
                            autoComplete="off"
                            data-1p-ignore
                            data-lpignore="true"
                            autoFocus
                            placeholder="Analytics replica"
                            value={name}
                            onChange={(event) => setName(event.target.value)}
                        />
                        <p className="text-xs leading-5 text-[var(--text-tertiary)]">
                            Shown wherever this connection is used, so agents and people pick the right one.
                        </p>
                    </div>

                    <SchemaFields
                        schema={configSchema}
                        values={config}
                        onChange={setConfig}
                        emptyMessage="This connection needs no further details."
                        followSchemaOrder
                    />

                    {showCredentials ? (
                        <div className="space-y-3 border-t border-[var(--border-subtle)] pt-4">
                            <Label>Credentials</Label>
                            <SchemaFields
                                schema={credentialSchema}
                                values={credentials}
                                onChange={setCredentials}
                                emptyMessage="This connection needs no credentials."
                                followSchemaOrder
                            />
                        </div>
                    ) : null}

                    {shownError ? (
                        <div className="state-surface-error flex items-start gap-2 rounded-lg px-3 py-2.5 text-xs leading-5 text-[var(--text-secondary)]">
                            <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--state-error)]" />
                            <span>{shownError}</span>
                        </div>
                    ) : null}
                </div>

                <DialogFooter>
                    <Button variant="secondary" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
                        Cancel
                    </Button>
                    <Button variant="primary" onClick={handleSubmit} disabled={isSubmitting}>
                        {isSubmitting ? <StepLoader size="sm" className="mr-2" /> : null}
                        {isEdit ? 'Save' : 'Add'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
