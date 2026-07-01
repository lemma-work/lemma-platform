'use client';

import { useEffect, useState } from 'react';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { AlertTriangle, Loader2 } from 'lucide-react';
import { buildSchemaFormPayload, buildSchemaFormValues } from 'lemma-sdk';
import { toast } from 'sonner';
import type { Connector } from '@/lib/types';
import { SchemaFields } from './schema-fields';
import { getAppLabel, getCredentialSchema, type ProviderCapability, type SchemaValues } from './connector-utils';

export interface CredentialTarget {
    connector: Connector;
    capability: ProviderCapability | null;
    authConfigId: string | null;
    mode: 'connect' | 'reconnect';
    accountId?: string;
}

export function ConnectAccountDialog({
    target,
    isSubmitting,
    onOpenChange,
    onSubmit,
}: {
    target: CredentialTarget | null;
    isSubmitting: boolean;
    onOpenChange: (open: boolean) => void;
    onSubmit: (data: Record<string, unknown>) => void;
}) {
    const schema = getCredentialSchema(target?.capability ?? null);
    const [values, setValues] = useState<SchemaValues>({});

    // Reset the form whenever the dialog opens for a different app / mode.
    useEffect(() => {
        if (target) {
            setValues(buildSchemaFormValues(schema));
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, [target?.connector.id, target?.mode]);

    const isReconnect = target?.mode === 'reconnect';

    const handleSubmit = () => {
        const payload = buildSchemaFormPayload(schema, values);
        if (!payload.isValid) {
            toast.error(Object.values(payload.errors)[0] || 'Credentials are incomplete');
            return;
        }
        onSubmit(payload.data);
    };

    return (
        <Dialog open={Boolean(target)} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>{isReconnect ? 'Reconnect account' : 'Connect account'}</DialogTitle>
                    <DialogDescription>
                        Enter the credentials for {getAppLabel(target?.connector)}. Fields come from the connector credential schema.
                    </DialogDescription>
                </DialogHeader>

                {isReconnect ? (
                    <div className="state-surface-warning flex items-start gap-2 rounded-lg px-3 py-2.5 text-xs leading-5 text-[var(--text-secondary)]">
                        <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--state-warning)]" />
                        <span>
                            Reconnecting re-links this account. Agents, workflows, and schedules that reference it may need
                            to be pointed at the new connection.
                        </span>
                    </div>
                ) : null}

                <div className="py-2">
                    <SchemaFields
                        schema={schema}
                        values={values}
                        onChange={setValues}
                        emptyMessage="No credential fields are required for this app."
                        autoFocusFirst
                    />
                </div>
                <DialogFooter>
                    <Button variant="outline" onClick={() => onOpenChange(false)} disabled={isSubmitting}>
                        Cancel
                    </Button>
                    <Button onClick={handleSubmit} disabled={isSubmitting}>
                        {isSubmitting ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null}
                        {isReconnect ? 'Reconnect' : 'Connect'}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
