import s from "./landing.module.css";

/** The marketing pages' icons: drawn here, one violet line each, with a
 *  little motion that says what the thing does. Agents twinkle, a dot
 *  travels a workflow, an app's bars rise, a table's rows light in turn, a
 *  page writes itself. With reduced motion they hold still. */
const DRAWINGS = {
    agents: (
        <svg viewBox="0 0 24 24" className={s.spark}>
            <path className={s.big} d="M10 3 L11.8 8.2 L17 10 L11.8 11.8 L10 17 L8.2 11.8 L3 10 L8.2 8.2 Z" />
            <path className={s.small} d="M18 15 L18.8 17.2 L21 18 L18.8 18.8 L18 21 L17.2 18.8 L15 18 L17.2 17.2 Z" />
        </svg>
    ),
    workflows: (
        <svg viewBox="0 0 24 24" className={s.flow}>
            <path d="M5 6 H12 V18 H19" opacity=".35" />
            <circle cx="5" cy="6" r="2.2" />
            <circle cx="19" cy="18" r="2.2" />
            <rect x="10" y="10" width="4" height="4" rx="1" />
            <circle className={`${s.dot} ${s.fill}`} r="1.8" />
        </svg>
    ),
    apps: (
        <svg viewBox="0 0 24 24" className={s.bars3}>
            <rect className={s.fill} x="5" y="13" width="3.4" height="7" rx="1" />
            <rect className={s.fill} x="10.3" y="8.5" width="3.4" height="11.5" rx="1" />
            <rect className={s.fill} x="15.6" y="4" width="3.4" height="16" rx="1" />
        </svg>
    ),
    tables: (
        <svg viewBox="0 0 24 24" className={s.rows}>
            <rect x="3.5" y="4" width="17" height="16" rx="2.5" />
            <rect className={`${s.row} ${s.fill}`} x="5" y="7.2" width="14" height="2.6" rx="1" />
            <rect className={`${s.row} ${s.fill}`} x="5" y="10.7" width="14" height="2.6" rx="1" />
            <rect className={`${s.row} ${s.fill}`} x="5" y="14.2" width="14" height="2.6" rx="1" />
        </svg>
    ),
    pages: (
        <svg viewBox="0 0 24 24" className={s.write}>
            <path d="M6 3h8l4 4v14H6z" />
            <path d="M14 3v4h4" opacity=".5" />
            <path className={s.line} d="M9 11h6" />
            <path className={s.line} d="M9 14h6" />
            <path className={s.line} d="M9 17h4" />
        </svg>
    ),
    plan: (
        <svg viewBox="0 0 24 24" className={s.plug}>
            <circle className={s.ring} cx="12" cy="12" r="9" opacity=".5" />
            <path d="M9 4v4M15 4v4M7 8h10v3a5 5 0 0 1-10 0zM12 16v4" />
        </svg>
    ),
    place: (
        <svg viewBox="0 0 24 24" className={s.cloud}>
            <g className={s.puff}><path d="M7 17h10a4 4 0 0 0 .5-8 5.5 5.5 0 0 0-10.6 1.5A3.3 3.3 0 0 0 7 17z" /></g>
            <path d="M9 21h6" opacity=".5" />
        </svg>
    ),
    seen: (
        <svg viewBox="0 0 24 24" className={s.eye}>
            <g className={s.lid}>
                <path d="M2.5 12s3.5-6 9.5-6 9.5 6 9.5 6-3.5 6-9.5 6-9.5-6-9.5-6z" />
                <circle cx="12" cy="12" r="2.6" />
            </g>
        </svg>
    ),
    kept: (
        <svg viewBox="0 0 24 24" className={s.folder}>
            <path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" opacity=".45" />
            <path className={s.front} d="M3 10h18v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" />
        </svg>
    ),
} as const;

export type IconName = keyof typeof DRAWINGS;

export function Icon({ name }: { name: IconName }) {
    return <span className={s.icon} aria-hidden="true">{DRAWINGS[name]}</span>;
}

/** The logo's three bars as a section mark, lit up to how far along it is. */
export function Bars({ lit }: { lit: 1 | 2 | 3 }) {
    return (
        <svg viewBox="0 0 32 32" aria-hidden="true">
            <rect x="4" y="19" width="5" height="10" rx="1.5" fill="currentColor" />
            <rect x="13" y="12" width="5" height="17" rx="1.5" fill="currentColor" opacity={lit >= 2 ? 1 : 0.3} />
            <rect x="22" y="3" width="5" height="26" rx="1.5" fill="currentColor" opacity={lit >= 3 ? 1 : 0.3} />
        </svg>
    );
}
