'use client';

import type { ConceptId } from '@/lib/education/concepts';
import { cn } from '@/lib/utils';

interface ConceptIllustrationProps {
    concept: ConceptId;
    className?: string;
}

/**
 * Small looping scenes that show each concept doing its job. Decorative —
 * always paired with the registry copy, hidden from screen readers, and
 * frozen under prefers-reduced-motion (see styles/features/education.css).
 */
export function ConceptIllustration({ concept, className }: ConceptIllustrationProps) {
    const scene = SCENES[concept];
    if (!scene) return null;

    return (
        <svg
            viewBox="0 0 240 120"
            aria-hidden="true"
            focusable="false"
            className={cn('edu-illo', className)}
        >
            {scene}
        </svg>
    );
}

export function hasConceptIllustration(concept: ConceptId): boolean {
    return SCENES[concept] !== undefined;
}

const SCENES: Partial<Record<ConceptId, React.ReactNode>> = {
    surface: (
        <>
            <circle className="edu-stroke" cx="34" cy="22" r="10" />
            <circle className="edu-stroke" cx="34" cy="60" r="10" />
            <circle className="edu-stroke" cx="34" cy="98" r="10" />
            <line className="edu-stroke" x1="46" y1="22" x2="166" y2="56" strokeDasharray="3 4" />
            <line className="edu-stroke" x1="46" y1="60" x2="166" y2="60" strokeDasharray="3 4" />
            <line className="edu-stroke" x1="46" y1="98" x2="166" y2="64" strokeDasharray="3 4" />
            <rect className="edu-fill-soft edu-surface-pod" x="170" y="38" width="52" height="44" rx="9" />
            <text className="edu-label" x="196" y="64" textAnchor="middle">pod</text>
            <text className="edu-label" x="14" y="63" textAnchor="middle" transform="rotate(-90 14 63)">surfaces</text>
            <circle className="edu-dot edu-surface-dot--1" cx="48" cy="22" r="3.5" />
            <circle className="edu-dot edu-surface-dot--2" cx="48" cy="60" r="3.5" />
            <circle className="edu-dot edu-surface-dot--3" cx="48" cy="98" r="3.5" />
        </>
    ),
    agent: (
        <>
            <line className="edu-stroke" x1="30" y1="60" x2="98" y2="60" strokeDasharray="3 4" />
            <line className="edu-stroke" x1="142" y1="60" x2="210" y2="60" strokeDasharray="3 4" />
            <circle className="edu-fill-soft edu-agent-core" cx="120" cy="60" r="20" />
            <path
                className="edu-spark edu-agent-core"
                d="M120 49l2.6 7.4 7.4 2.6-7.4 2.6-2.6 7.4-2.6-7.4-7.4-2.6 7.4-2.6z"
            />
            <rect className="edu-fill-soft edu-agent-in" x="24" y="52" width="28" height="16" rx="4" />
            <g className="edu-agent-out">
                <rect className="edu-fill-soft" x="148" y="50" width="34" height="20" rx="4" />
                <line className="edu-stroke" x1="153" y1="57" x2="177" y2="57" strokeWidth="1" />
                <line className="edu-stroke" x1="153" y1="63" x2="171" y2="63" strokeWidth="1" />
            </g>
            <text className="edu-label" x="38" y="86" textAnchor="middle">work in</text>
            <text className="edu-label" x="120" y="98" textAnchor="middle">agent</text>
            <text className="edu-label" x="196" y="86" textAnchor="middle">result out</text>
        </>
    ),
    flow: (
        <>
            <line className="edu-stroke" x1="50" y1="60" x2="98" y2="60" />
            <line className="edu-stroke" x1="110" y1="60" x2="158" y2="60" />
            <line className="edu-stroke" x1="170" y1="60" x2="208" y2="60" />
            <circle className="edu-fill-soft edu-flow-node-1" cx="40" cy="60" r="10" />
            <circle className="edu-fill-soft edu-flow-node-2" cx="104" cy="60" r="10" />
            <circle className="edu-fill-soft edu-flow-node-3" cx="164" cy="60" r="10" />
            <circle className="edu-flow-approval" cx="164" cy="60" r="16" />
            <circle className="edu-fill-soft edu-flow-node-4" cx="214" cy="60" r="10" />
            <text className="edu-label" x="40" y="88" textAnchor="middle">trigger</text>
            <text className="edu-label" x="104" y="88" textAnchor="middle">agent</text>
            <text className="edu-label" x="164" y="92" textAnchor="middle">approve</text>
            <text className="edu-label" x="214" y="88" textAnchor="middle">done</text>
        </>
    ),
    table: (
        <>
            <rect className="edu-fill-soft" x="40" y="18" width="160" height="16" rx="4" />
            <line className="edu-stroke" x1="104" y1="22" x2="104" y2="30" strokeWidth="1" />
            <line className="edu-stroke" x1="152" y1="22" x2="152" y2="30" strokeWidth="1" />
            <g className="edu-table-row-1">
                <rect className="edu-stroke" x="40" y="42" width="160" height="16" rx="4" />
                <circle className="edu-dot" cx="52" cy="50" r="3" />
            </g>
            <g className="edu-table-row-2">
                <rect className="edu-stroke" x="40" y="66" width="160" height="16" rx="4" />
                <circle className="edu-dot" cx="52" cy="74" r="3" />
            </g>
            <g className="edu-table-row-3">
                <rect className="edu-stroke" x="40" y="90" width="160" height="16" rx="4" />
                <circle className="edu-dot" cx="52" cy="98" r="3" />
            </g>
        </>
    ),
    app: (
        <>
            <rect className="edu-stroke" x="40" y="16" width="160" height="88" rx="9" />
            <line className="edu-stroke" x1="40" y1="34" x2="200" y2="34" />
            <circle className="edu-dot" cx="52" cy="25" r="2.5" />
            <rect className="edu-fill-soft edu-app-bar-1" x="54" y="44" width="132" height="10" rx="3" />
            <line className="edu-stroke" x1="54" y1="64" x2="186" y2="64" strokeWidth="1" />
            <path className="edu-spark edu-app-agent" d="M64 80l2.2 6 6 2.2-6 2.2-2.2 6-2.2-6-6-2.2 6-2.2z" />
            <rect className="edu-fill-soft edu-app-type-1" x="80" y="73" width="104" height="8" rx="3" />
            <rect className="edu-fill-soft edu-app-type-2" x="80" y="88" width="80" height="8" rx="3" />
            <text className="edu-label" x="120" y="116" textAnchor="middle">agents work alongside you</text>
        </>
    ),
    kit: (
        <>
            <rect className="edu-fill-soft" x="24" y="42" width="46" height="38" rx="7" />
            <line className="edu-stroke" x1="24" y1="54" x2="70" y2="54" strokeWidth="1" />
            <rect className="edu-fill-soft" x="40" y="35" width="14" height="9" rx="2" />
            <text className="edu-label" x="47" y="96" textAnchor="middle">kit</text>
            <line className="edu-stroke" x1="76" y1="60" x2="96" y2="60" />
            <path className="edu-stroke" d="M92 56l5 4-5 4" fill="none" />
            <g className="edu-kit-pop-1">
                <rect className="edu-fill-soft" x="104" y="28" width="50" height="20" rx="6" />
                <text className="edu-label" x="129" y="41" textAnchor="middle">app</text>
            </g>
            <g className="edu-kit-pop-2">
                <rect className="edu-fill-soft" x="164" y="28" width="58" height="20" rx="6" />
                <text className="edu-label" x="193" y="41" textAnchor="middle">agent</text>
            </g>
            <g className="edu-kit-pop-3">
                <rect className="edu-fill-soft" x="104" y="56" width="50" height="20" rx="6" />
                <text className="edu-label" x="129" y="69" textAnchor="middle">table</text>
            </g>
            <g className="edu-kit-pop-4">
                <rect className="edu-fill-soft" x="164" y="56" width="66" height="20" rx="6" />
                <text className="edu-label" x="197" y="69" textAnchor="middle">schedule</text>
            </g>
        </>
    ),
    function: (
        <>
            <line className="edu-stroke" x1="28" y1="60" x2="94" y2="60" strokeDasharray="3 4" />
            <line className="edu-stroke" x1="146" y1="60" x2="212" y2="60" strokeDasharray="3 4" />
            <rect className="edu-fn-box" x="96" y="40" width="48" height="40" rx="7" />
            <text className="edu-label" x="120" y="64" textAnchor="middle">f(x)</text>
            <circle className="edu-dot edu-fn-dot" cx="28" cy="60" r="3.5" />
            <text className="edu-label" x="28" y="80" textAnchor="middle">same in</text>
            <text className="edu-label" x="206" y="80" textAnchor="middle">same out</text>
        </>
    ),
    schedule: (
        <>
            <circle className="edu-stroke" cx="60" cy="60" r="18" />
            <line className="edu-stroke edu-clock-hand" x1="60" y1="60" x2="60" y2="46" />
            <circle className="edu-dot" cx="60" cy="60" r="2" />
            <line className="edu-stroke" x1="82" y1="60" x2="186" y2="60" strokeDasharray="3 4" />
            <circle className="edu-dot edu-clock-dot" cx="82" cy="60" r="3.5" />
            <rect className="edu-fill-soft edu-clock-node" x="190" y="46" width="28" height="28" rx="6" />
            <text className="edu-label" x="60" y="94" textAnchor="middle">every morning</text>
            <text className="edu-label" x="204" y="92" textAnchor="middle">run</text>
        </>
    ),
    connector: (
        <>
            <rect className="edu-fill-soft" x="28" y="42" width="44" height="36" rx="7" />
            <rect className="edu-fill-soft" x="168" y="42" width="44" height="36" rx="7" />
            <text className="edu-label" x="50" y="64" textAnchor="middle">pod</text>
            <text className="edu-label" x="190" y="64" textAnchor="middle">app</text>
            <line className="edu-stroke" x1="76" y1="60" x2="164" y2="60" />
            <line className="edu-stroke" x1="84" y1="52" x2="84" y2="68" strokeWidth="2.5" />
            <line className="edu-stroke" x1="156" y1="52" x2="156" y2="68" strokeWidth="2.5" />
            <circle className="edu-dot edu-int-ping" cx="84" cy="60" r="3.5" />
            <circle className="edu-spark edu-int-pong" cx="156" cy="60" r="3.5" />
        </>
    ),
};
