'use client';

import Link from 'next/link';
import { CheckCircle2, Circle, X } from '@/components/ui/icons';
import * as DropdownMenu from '@radix-ui/react-dropdown-menu';

import { getConcept } from '@/lib/education/concepts';
import { useEducationKey, useMarkEducation } from '@/lib/education/use-education-state';
import { useAppPages } from '@/lib/hooks/use-app';
import { usePodAccess } from '@/lib/hooks/use-pod-access';
import { usePodSurfaces } from '@/lib/hooks/use-pod-surfaces';
import { cn } from '@/lib/utils';

// Pod-scoped: the checklist is about onboarding one specific pod, so a fresh
// pod gets its own checklist even if you dismissed it elsewhere. (Primers and
// tours stay global — they teach concepts and editor UI that don't change per pod.)
const checklistKey = (podId: string) => `checklist:first-win:${podId}`;

interface FirstWinChecklistProps {
    podId: string;
    agentCount: number;
    workflowCount: number;
    conversationCount: number;
}

interface ChecklistItem {
    id: string;
    label: string;
    description: string;
    href: string | null;
    done: boolean;
}

function ProgressRing({ done, total }: { done: number; total: number }) {
    const radius = 7;
    const circumference = 2 * Math.PI * radius;
    const progress = total > 0 ? done / total : 0;
    return (
        <svg width="18" height="18" viewBox="0 0 18 18" className="shrink-0" aria-hidden="true">
            <circle cx="9" cy="9" r={radius} fill="none" stroke="var(--bg-subtle)" strokeWidth="2" />
            <circle
                cx="9"
                cy="9"
                r={radius}
                fill="none"
                stroke="var(--delight)"
                strokeWidth="2"
                strokeLinecap="round"
                strokeDasharray={circumference}
                strokeDashoffset={circumference * (1 - progress)}
                transform="rotate(-90 9 9)"
            />
        </svg>
    );
}

/**
 * The pod's path to a first win, driven by real resource counts. Lives as a
 * compact progress pill pinned to the pod-home header — out of the document flow
 * so it never shifts the page as data loads — and expands to the full step list
 * on click. Disappears once everything is done or the user dismisses it.
 */
export function FirstWinChecklist({ podId, agentCount, workflowCount, conversationCount }: FirstWinChecklistProps) {
    const podAccess = usePodAccess(podId);
    const canReadSurfaces = podAccess.can('agent.read') || podAccess.can('agent.update') || podAccess.can('connector_account.manage');
    const { data: surfaces = [] } = usePodSurfaces(canReadSurfaces ? podId : undefined);
    const { pages: appPages = [] } = useAppPages(podId);
    const dismissed = useEducationKey(checklistKey(podId)) !== undefined;
    const markEducation = useMarkEducation();

    const hasActiveSurface = surfaces.some((surface) => surface.status === 'ACTIVE');

    const items: ChecklistItem[] = [
        {
            id: 'agent',
            label: 'Hire your first agent',
            description: getConcept('agent').oneLiner,
            href: `/pod/${podId}/agents/new`,
            done: agentCount > 0,
        },
        {
            id: 'conversation',
            label: 'Run it once from chat',
            description: 'Ask for something real in the box above and watch the conversation work.',
            href: null,
            done: conversationCount > 0,
        },
        {
            id: 'surface',
            label: 'Connect a surface',
            description: getConcept('surface').oneLiner,
            href: `/pod/${podId}/surfaces`,
            done: hasActiveSurface,
        },
        {
            id: 'structure',
            label: 'Add a recipe or build a quick agentic app',
            description: 'A recipe adds capability — a prompt the assistant builds, or a full kit. Or build an app yourself.',
            href: `/pod/${podId}/recipes`,
            done: appPages.length > 0 || workflowCount > 0,
        },
    ];

    // Builder-only: operators work a pod, they don't set it up.
    const doneCount = items.filter((item) => item.done).length;
    if (!podAccess.isBuilder || dismissed || doneCount === items.length) return null;

    const dismiss = () => markEducation(checklistKey(podId));

    return (
        <div className="pointer-events-none absolute right-5 top-4 z-30 sm:right-6">
            <DropdownMenu.Root>
                <DropdownMenu.Trigger asChild>
                    <button
                        type="button"
                        className="pointer-events-auto inline-flex items-center gap-2 rounded-full border border-[var(--border-subtle)] bg-[var(--surface-1)] py-1 pl-1 pr-3 text-xs font-medium text-[var(--text-secondary)] shadow-[var(--shadow-xs)] transition-colors hover:border-[var(--field-border)] hover:text-[var(--text-primary)] focus-ring"
                        aria-label={`Pod setup, ${doneCount} of ${items.length} done`}
                    >
                        <ProgressRing done={doneCount} total={items.length} />
                        <span>Set up</span>
                        <span className="text-[var(--text-tertiary)]">{doneCount}/{items.length}</span>
                    </button>
                </DropdownMenu.Trigger>
                <DropdownMenu.Portal>
                    <DropdownMenu.Content
                        align="end"
                        sideOffset={8}
                        className="surface-panel z-50 w-80 p-3 shadow-[var(--shadow-lg)]"
                    >
                        <div className="flex items-start justify-between gap-2">
                            <div className="min-w-0">
                                <h3 className="text-sm font-medium text-[var(--text-primary)]">Get this pod working</h3>
                                <p className="mt-0.5 text-xs text-[var(--text-tertiary)]">{doneCount} of {items.length} done</p>
                            </div>
                            <button
                                type="button"
                                aria-label="Dismiss checklist"
                                onClick={dismiss}
                                className="-mr-1 -mt-1 inline-flex h-7 w-7 shrink-0 items-center justify-center rounded-md text-[var(--text-tertiary)] transition-gentle hover:bg-[var(--surface-2)] hover:text-[var(--text-primary)] focus-ring"
                            >
                                <X className="h-3.5 w-3.5" aria-hidden="true" />
                            </button>
                        </div>
                        <ol className="mt-3 space-y-0.5">
                            {items.map((item) => {
                                const inner = (
                                    <span className="flex items-start gap-2.5">
                                        {item.done ? (
                                            <CheckCircle2 className="mt-0.5 h-4 w-4 shrink-0 text-[var(--delight)]" aria-hidden="true" />
                                        ) : (
                                            <Circle className="mt-0.5 h-4 w-4 shrink-0 text-[var(--text-tertiary)]" aria-hidden="true" />
                                        )}
                                        <span className="min-w-0">
                                            <span
                                                className={cn(
                                                    'block text-sm font-medium',
                                                    item.done ? 'text-[var(--text-tertiary)] line-through' : 'text-[var(--text-primary)]',
                                                )}
                                            >
                                                {item.label}
                                            </span>
                                            <span className="mt-0.5 block text-xs leading-5 text-[var(--text-tertiary)]">
                                                {item.description}
                                            </span>
                                        </span>
                                    </span>
                                );

                                return (
                                    <li key={item.id}>
                                        {item.href && !item.done ? (
                                            <Link
                                                href={item.href}
                                                className="block rounded-md px-2 py-1.5 transition-gentle hover:bg-[var(--surface-2)]"
                                            >
                                                {inner}
                                            </Link>
                                        ) : (
                                            <span className="block px-2 py-1.5">{inner}</span>
                                        )}
                                    </li>
                                );
                            })}
                        </ol>
                    </DropdownMenu.Content>
                </DropdownMenu.Portal>
            </DropdownMenu.Root>
        </div>
    );
}
