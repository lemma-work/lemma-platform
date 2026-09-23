'use client';

import Link from 'next/link';
import { useMemo, useState } from 'react';
import { ArrowRight } from '@/components/ui/icons';

import { ProductIcon } from '@/components/pod/product-icon';
import { CONCEPTS, type ConceptId } from '@/lib/education/concepts';
import { readLastOpenedPodId } from '@/lib/pods/last-opened-pod';

type MapNodeSpec = {
    concept: ConceptId;
    label: string;
    x: number;
    y: number;
    w: number;
    h: number;
    podSection: string | null;
    small?: boolean;
};

const NODES: MapNodeSpec[] = [
    { concept: 'surface', label: 'Surfaces', x: 60, y: 200, w: 130, h: 64, podSection: 'surfaces' },
    { concept: 'agent', label: 'Agents', x: 280, y: 110, w: 120, h: 56, podSection: 'ai' },
    { concept: 'flow', label: 'Workflows', x: 440, y: 110, w: 120, h: 56, podSection: 'flows' },
    { concept: 'function', label: 'Functions', x: 600, y: 110, w: 104, h: 56, podSection: 'functions' },
    { concept: 'approval', label: 'Approval', x: 455, y: 196, w: 90, h: 34, podSection: 'flows', small: true },
    { concept: 'table', label: 'Tables', x: 320, y: 300, w: 120, h: 56, podSection: 'data' },
    { concept: 'file', label: 'Files', x: 520, y: 300, w: 120, h: 56, podSection: 'files' },
    { concept: 'app', label: 'Apps', x: 780, y: 120, w: 130, h: 56, podSection: 'app/pages' },
    { concept: 'conversation', label: 'Conversations', x: 780, y: 240, w: 140, h: 56, podSection: 'conversations' },
    { concept: 'schedule', label: 'Schedules', x: 300, y: 470, w: 120, h: 50, podSection: 'schedules' },
    { concept: 'connector', label: 'Connectors', x: 540, y: 470, w: 130, h: 50, podSection: 'connectors' },
];

const EDGES: Array<{ d: string; both?: boolean }> = [
    { d: 'M190 232 L232 232' },
    { d: 'M340 166 L372 292' },
    { d: 'M380 166 L520 320' },
    { d: 'M500 166 L500 188' },
    { d: 'M724 148 L772 148' },
    { d: 'M724 268 L772 268' },
    { d: 'M360 462 L360 426' },
    { d: 'M605 462 L605 426', both: true },
];

export function HowLemmaWorksMap() {
    const [active, setActive] = useState<ConceptId>('pod');
    const entry = CONCEPTS[active];
    const podId = useMemo(() => readLastOpenedPodId(), []);

    const activeNode = NODES.find((node) => node.concept === active);
    const podHref = activeNode?.podSection && podId ? `/pod/${podId}/${activeNode.podSection}` : active === 'pod' && podId ? `/pod/${podId}` : null;

    return (
        <div className="grid gap-5">
            <div className="surface-panel overflow-hidden p-2 md:p-4">
                <svg viewBox="0 0 960 540" className="block h-auto w-full" role="img" aria-label="Map of how Lemma works: surfaces feed work into a pod where agents, workflows, and functions act over tables and files; people steer from apps and conversations; schedules and connectors sit underneath.">
                    <defs>
                        <marker id="hlw-arrow" viewBox="0 0 10 10" refX="8" refY="5" markerWidth="7" markerHeight="7" orient="auto-start-reverse">
                            <path d="M1.5 1.5L8 5L1.5 8.5" fill="none" stroke="var(--text-tertiary)" strokeWidth="1.4" strokeLinecap="round" strokeLinejoin="round" />
                        </marker>
                    </defs>

                    <g
                        onMouseEnter={() => setActive('pod')}
                        onClick={() => setActive('pod')}
                        className="cursor-pointer"
                    >
                        <rect
                            x="240" y="50" width="484" height="376" rx="18"
                            fill="color-mix(in srgb, var(--surface-1) 55%, transparent)"
                            stroke={active === 'pod' ? 'var(--delight)' : 'var(--border-subtle)'}
                            strokeWidth="1.5" strokeDasharray="7 6"
                        />
                        <text x="262" y="82" fill={active === 'pod' ? 'var(--delight)' : 'var(--text-tertiary)'} fontSize="14" fontWeight="600" className="uppercase tracking-widest">
                            Pod
                        </text>
                    </g>

                    {EDGES.map((edge) => (
                        <path
                            key={edge.d}
                            d={edge.d}
                            fill="none"
                            stroke="var(--text-tertiary)"
                            strokeWidth="1.4"
                            markerEnd="url(#hlw-arrow)"
                            markerStart={edge.both ? 'url(#hlw-arrow)' : undefined}
                            opacity="0.6"
                        />
                    ))}

                    {NODES.map((node) => {
                        const isActive = active === node.concept;
                        return (
                            <g
                                key={node.concept}
                                onMouseEnter={() => setActive(node.concept)}
                                onClick={() => setActive(node.concept)}
                                className="cursor-pointer"
                                role="button"
                                aria-label={`${node.label}: ${CONCEPTS[node.concept].oneLiner}`}
                            >
                                <rect
                                    x={node.x} y={node.y} width={node.w} height={node.h} rx={node.small ? 17 : 12}
                                    fill={isActive ? 'var(--delight-soft)' : 'var(--surface-1)'}
                                    stroke={isActive ? 'var(--delight)' : 'var(--border-strong, var(--border-subtle))'}
                                    strokeWidth="1.5"
                                />
                                <text
                                    x={node.x + node.w / 2}
                                    y={node.y + node.h / 2}
                                    textAnchor="middle"
                                    dominantBaseline="central"
                                    fill={isActive ? 'var(--text-primary)' : 'var(--text-secondary)'}
                                    fontSize={node.small ? 13 : 15}
                                    fontWeight="600"
                                >
                                    {node.label}
                                </text>
                            </g>
                        );
                    })}
                </svg>
            </div>

            <div className="surface-panel flex flex-col gap-3 p-5 md:flex-row md:items-start md:justify-between">
                <div className="flex min-w-0 items-start gap-3">
                    <div className="mt-0.5 shrink-0">
                        <ProductIcon kind={entry.iconKind} size="md" />
                    </div>
                    <div className="min-w-0">
                        <h3 className="text-base font-semibold text-[var(--text-primary)]">{entry.term}</h3>
                        <p className="mt-1 text-sm leading-6 text-[var(--text-secondary)]">{entry.oneLiner}</p>
                        <p className="mt-2 max-w-2xl text-sm leading-6 text-[var(--text-tertiary)]">{entry.explainer[0]}</p>
                    </div>
                </div>
                <div className="flex shrink-0 flex-wrap items-center gap-2 md:flex-col md:items-end">
                    {active !== 'pod' ? (
                        <Link
                            className="inline-flex items-center gap-1 rounded-lg border border-[var(--border-subtle)] px-3 py-1.5 text-sm font-medium text-[var(--text-primary)] hover:border-[var(--delight)]"
                            href={`/docs/${entry.guideSlug}`}
                        >
                            Concept guide
                            <ArrowRight className="h-3.5 w-3.5" />
                        </Link>
                    ) : null}
                    {podHref ? (
                        <Link
                            className="inline-flex items-center gap-1 rounded-lg bg-[var(--button-primary-bg)] px-3 py-1.5 text-sm font-semibold text-[var(--button-primary-fg)] hover:bg-[var(--button-primary-bg-hover)]"
                            href={podHref}
                        >
                            Open in your pod
                            <ArrowRight className="h-3.5 w-3.5" />
                        </Link>
                    ) : null}
                </div>
            </div>

            <p className="text-sm leading-6 text-[var(--text-tertiary)]">
                One distinction worth holding on to: every worker in this map carries two separate controls. Its
                access scope decides what it can touch (the arrows into tables and files); its sharing decides who
                can see and use it. The Share button governs people; the Access section governs the worker.
            </p>
        </div>
    );
}
