'use client';

import type { ReactNode } from 'react';
import { MoreHorizontal, Trash2 } from '@/components/ui/icons';

import { Button } from '@/components/ui/button';
import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { cn } from '@/lib/utils';

type ResourceActionsMenuProps = {
    ariaLabel: string;
    children: ReactNode;
    className?: string;
    triggerClassName?: string;
    align?: 'start' | 'center' | 'end';
};

export function ResourceActionsMenu({
    ariaLabel,
    children,
    className,
    triggerClassName,
    align = 'end',
}: ResourceActionsMenuProps) {
    return (
        <DropdownMenu>
            <DropdownMenuTrigger asChild>
                <Button
                    variant="ghost"
                    size="icon"
                    className={cn('h-8 w-8 shrink-0 text-[var(--text-tertiary)] hover:text-[var(--text-secondary)]', triggerClassName)}
                    aria-label={ariaLabel}
                >
                    <MoreHorizontal className="h-4 w-4" />
                </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align={align} className={className}>
                {children}
            </DropdownMenuContent>
        </DropdownMenu>
    );
}

type DestructiveResourceActionItemProps = {
    children: ReactNode;
    onSelect: () => void;
    disabled?: boolean;
    icon?: ReactNode;
    className?: string;
};

export function DestructiveResourceActionItem({
    children,
    onSelect,
    disabled,
    icon,
    className,
}: DestructiveResourceActionItemProps) {
    return (
        <DropdownMenuItem
            disabled={disabled}
            className={cn('text-[var(--state-error)] focus:text-[var(--state-error)]', className)}
            onSelect={(event) => {
                event.preventDefault();
                onSelect();
            }}
        >
            {icon ?? <Trash2 className="mr-2 h-4 w-4" />}
            {children}
        </DropdownMenuItem>
    );
}
