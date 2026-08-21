import { FORMS, LEM_FORM } from '@/lib/identity/seeded-identity';
import type { PodWelcomeCardId } from '@/lib/pods/pod-welcome';

/**
 * A mark per option, sized for a row rather than a panel.
 *
 * These were detailed drawings — a phone mid-conversation, a page with a
 * sidebar, a roster with a slot open — back when each option owned a 92px
 * picture panel. Lem owns the picture now, and an option gets a 34px tile, at
 * which size those drawings stopped being different from each other: three
 * shapes in a row reads the same whether it means agents or people.
 *
 * So the tiles carry silhouettes instead, on the one rule that survives the
 * shrink. The agent mark is Lem's own reserved body from `FORMS`, the only
 * shape in the system that is concave — which is what keeps "another agent"
 * from being read as "another person" at this size.
 */

const LEM_BODY = FORMS[LEM_FORM];

const STROKE = {
    fill: 'none',
    stroke: 'currentColor',
    strokeWidth: 1.7,
    strokeLinecap: 'round',
    strokeLinejoin: 'round',
} as const;

/** Where a conversation already happens. */
function SurfaceMark() {
    return (
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" {...STROKE}>
            <path d="M21 11.5a8.4 8.4 0 0 1-8.5 8.4 8.7 8.7 0 0 1-3.9-.9L3 21l1.9-5.4A8.3 8.3 0 0 1 4 11.5 8.4 8.4 0 0 1 12.5 3 8.4 8.4 0 0 1 21 11.5Z" />
        </svg>
    );
}

/** A page: header, sidebar, content. The smallest drawing that still says screen. */
function AppMark() {
    return (
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" {...STROKE}>
            <rect x="3" y="4" width="18" height="16" rx="2" />
            <path d="M3 9h18M8 13h5M8 16h7" />
        </svg>
    );
}

/** Lem's body and a plus: a colleague, not a contact. */
function AgentMark() {
    return (
        <svg width="19" height="19" viewBox="0 0 100 100" fill="none" aria-hidden="true">
            <g transform="translate(-8 2) scale(0.82)">
                <path d={LEM_BODY} stroke="currentColor" strokeWidth="7" strokeLinejoin="round" />
            </g>
            <path d="M78 60v26M65 73h26" stroke="currentColor" strokeWidth="9" strokeLinecap="round" />
        </svg>
    );
}

/** Two people, because this is the one option that is not about an agent. */
function PeopleMark() {
    return (
        <svg width="18" height="18" viewBox="0 0 24 24" aria-hidden="true" {...STROKE}>
            <circle cx="9" cy="8" r="3.2" />
            <path d="M3 20a6 6 0 0 1 12 0" />
            <path d="M16.5 5.5a3.2 3.2 0 0 1 0 5M18 20a6 6 0 0 0-2.2-4.6" />
        </svg>
    );
}

export const POD_WELCOME_ART: Record<PodWelcomeCardId, () => React.JSX.Element> = {
    surface: SurfaceMark,
    app: AppMark,
    agent: AgentMark,
    people: PeopleMark,
};
