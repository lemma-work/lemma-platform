'use client';

import { useId, useState, type ReactNode } from 'react';
import { AlertTriangle } from '@/components/ui/icons';

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
import { cn } from '@/lib/utils';

type DestructiveConfirmationDialogProps = {
    open: boolean;
    onOpenChange: (open: boolean) => void;
    title: string;
    description: string;
    resourceName: string;
    consequences?: string[];
    confirmationText?: string;
    confirmationLabel?: string;
    confirmLabel?: string;
    pendingLabel?: string;
    isPending?: boolean;
    onConfirm: () => void;
    icon?: ReactNode;
};

export function DestructiveConfirmationDialog({
    open,
    onOpenChange,
    title,
    description,
    resourceName,
    consequences = [],
    confirmationText = resourceName,
    confirmationLabel,
    confirmLabel = 'Delete',
    pendingLabel = 'Deleting...',
    isPending = false,
    onConfirm,
    icon,
}: DestructiveConfirmationDialogProps) {
    const inputId = useId();
    const requiredText = confirmationText.trim();
    const typedKey = open ? requiredText : '';
    const [typedDraft, setTypedDraft] = useState({ key: '', value: '' });
    const typedValue = typedDraft.key === typedKey ? typedDraft.value : '';
    const canConfirm = requiredText.length === 0 || typedValue.trim() === requiredText;

    return (
        <Dialog
            open={open}
            onOpenChange={(nextOpen) => {
                if (!nextOpen) setTypedDraft({ key: '', value: '' });
                if (!isPending) onOpenChange(nextOpen);
            }}
        >
            <DialogContent className="gap-5">
                <DialogHeader className="gap-3">
                    <div className="flex items-start gap-3">
                        <span className="state-badge-error flex h-9 w-9 shrink-0 items-center justify-center rounded-lg">
                            {icon ?? <AlertTriangle className="h-4 w-4" />}
                        </span>
                        <div className="min-w-0">
                            <DialogTitle>{title}</DialogTitle>
                            <DialogDescription className="mt-1">
                                {description}
                            </DialogDescription>
                        </div>
                    </div>
                </DialogHeader>

                {consequences.length > 0 ? (
                    <div className="surface-panel-muted rounded-md px-3 py-2.5">
                        <ul className="space-y-1.5 text-sm leading-6 text-[var(--text-secondary)]">
                            {consequences.map((consequence) => (
                                <li key={consequence} className="flex gap-2">
                                    <span className="mt-2 h-1 w-1 shrink-0 rounded-full bg-[var(--state-error)]" />
                                    <span>{consequence}</span>
                                </li>
                            ))}
                        </ul>
                    </div>
                ) : null}

                {requiredText.length > 0 ? (
                    <div className="space-y-2">
                        <label htmlFor={inputId} className="block text-sm font-medium text-[var(--text-primary)]">
                            {confirmationLabel ?? (
                                <>
                                    Type <span className="font-semibold text-[var(--state-error)]">&quot;{requiredText}&quot;</span> to continue.
                                </>
                            )}
                        </label>
                        <Input
                            id={inputId}
                            value={typedValue}
                            onChange={(event) => setTypedDraft({ key: typedKey, value: event.target.value })}
                            disabled={isPending}
                            autoComplete="off"
                            spellCheck={false}
                            className={cn(
                                'font-mono text-sm',
                                typedValue.length > 0 && !canConfirm && 'border-[color:var(--state-error)]'
                            )}
                        />
                    </div>
                ) : null}

                <DialogFooter>
                    <Button variant="ghost" onClick={() => onOpenChange(false)} disabled={isPending}>
                        Cancel
                    </Button>
                    <Button
                        variant="destructive"
                        onClick={onConfirm}
                        disabled={isPending || !canConfirm}
                    >
                        {isPending ? pendingLabel : confirmLabel}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
