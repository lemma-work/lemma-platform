'use client';

import Link from 'next/link';
import { useEffect, useState } from 'react';
import { BookA, BookOpen, CircleHelp, Footprints, Map, RotateCcw } from '@/components/ui/icons';
import { toast } from 'sonner';

import {
    DropdownMenu,
    DropdownMenuContent,
    DropdownMenuItem,
    DropdownMenuLabel,
    DropdownMenuSeparator,
    DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu';
import { removeEducationKeysWithPrefix, resetEducation } from '@/lib/education/education-store';
import { cn } from '@/lib/utils';

interface HelpMenuProps {
    /** 'shell' matches the pod topbar icon buttons; 'header' matches the dashboard header. */
    variant?: 'shell' | 'header';
    className?: string;
}

export function HelpMenu({ variant = 'shell', className }: HelpMenuProps) {
    const [open, setOpen] = useState(false);

    useEffect(() => {
        const handleKeyDown = (event: KeyboardEvent) => {
            if (event.key === '/' && (event.metaKey || event.ctrlKey)) {
                event.preventDefault();
                setOpen((current) => !current);
            }
        };
        window.addEventListener('keydown', handleKeyDown);
        return () => window.removeEventListener('keydown', handleKeyDown);
    }, []);

    const handleReset = () => {
        resetEducation();
        toast.success('Hints and primers reset', {
            description: 'First-visit explainers will show again as you browse.',
        });
    };

    return (
        <DropdownMenu open={open} onOpenChange={setOpen}>
            <DropdownMenuTrigger asChild>
                <button
                    type="button"
                    aria-label="Help and learning"
                    title="Help (⌘/)"
                    className={cn(
                        variant === 'shell'
                            ? 'lemma-shell-icon-button custom-focus-ring'
                            : 'inline-flex h-9 w-9 items-center justify-center rounded-md text-[var(--text-tertiary)] transition-gentle hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)] focus-ring',
                        className
                    )}
                >
                    <CircleHelp className="h-4 w-4" strokeWidth={1.8} />
                </button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="end" className="w-64">
                <DropdownMenuLabel className="text-xs font-medium uppercase text-[var(--text-tertiary)]">
                    Learn Lemma
                </DropdownMenuLabel>
                <DropdownMenuItem asChild>
                    <Link href="/docs/how-lemma-works" target="_blank" rel="noopener noreferrer" className="flex cursor-pointer items-center gap-2">
                        <Map className="h-4 w-4 text-[var(--text-tertiary)]" />
                        How Lemma works
                    </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                    <Link href="/docs/glossary" target="_blank" rel="noopener noreferrer" className="flex cursor-pointer items-center gap-2">
                        <BookA className="h-4 w-4 text-[var(--text-tertiary)]" />
                        Glossary
                    </Link>
                </DropdownMenuItem>
                <DropdownMenuItem asChild>
                    <Link href="/docs" target="_blank" rel="noopener noreferrer" className="flex cursor-pointer items-center gap-2">
                        <BookOpen className="h-4 w-4 text-[var(--text-tertiary)]" />
                        Browse documentation
                    </Link>
                </DropdownMenuItem>
                <DropdownMenuSeparator />
                <DropdownMenuItem
                    onClick={() => {
                        removeEducationKeysWithPrefix('coachmarks:');
                        toast.success('Feature tours re-armed', {
                            description: 'Editors will walk you through their key controls again.',
                        });
                    }}
                    className="flex cursor-pointer items-center gap-2"
                >
                    <Footprints className="h-4 w-4 text-[var(--text-tertiary)]" />
                    Replay feature tours
                </DropdownMenuItem>
                <DropdownMenuItem onClick={handleReset} className="flex cursor-pointer items-center gap-2">
                    <RotateCcw className="h-4 w-4 text-[var(--text-tertiary)]" />
                    Reset hints and primers
                </DropdownMenuItem>
            </DropdownMenuContent>
        </DropdownMenu>
    );
}
