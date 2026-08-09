import Link from 'next/link';
import type { ReactNode } from 'react';

import { AlertTriangle, ArrowRight, CheckCircle, Info, Lightbulb } from '@/components/ui/icons';

/**
 * The vocabulary an MDX document can draw on.
 *
 * This is the part that decides whether the docs read as a product or as a
 * wiki. The block union it replaces could only express what was anticipated
 * when the union was written; here a writer composes from a fixed set of
 * designed pieces, and adding a new one is a component rather than a change to
 * a type every existing page depends on.
 *
 * Everything is a server component. None of these need state, and prose that
 * ships as HTML is the whole reason the pipeline compiles at build time.
 * Presentation lives in `styles/features/content.css` — the class names here
 * are the contract between the two.
 */

const CALLOUT_TONES = {
    note: { icon: Info, label: 'Note' },
    tip: { icon: Lightbulb, label: 'Tip' },
    warning: { icon: AlertTriangle, label: 'Warning' },
    success: { icon: CheckCircle, label: 'Success' },
} as const;

export type CalloutTone = keyof typeof CALLOUT_TONES;

export function Callout({
    tone = 'note',
    title,
    children,
}: {
    tone?: CalloutTone;
    title?: string;
    children: ReactNode;
}) {
    const { icon: Icon, label } = CALLOUT_TONES[tone] ?? CALLOUT_TONES.note;
    return (
        <aside className={`content-callout content-callout-${tone}`}>
            <Icon className="content-callout-icon" aria-hidden />
            <div className="content-callout-body">
                {/* The tone is carried by colour, which a screen reader cannot
                    see. The label restores it without printing a redundant
                    heading for sighted readers. */}
                <span className="sr-only">{label}: </span>
                {title ? <p className="content-callout-title">{title}</p> : null}
                {children}
            </div>
        </aside>
    );
}

export function Steps({ children }: { children: ReactNode }) {
    return <ol className="content-steps">{children}</ol>;
}

export function Step({ title, children }: { title: string; children: ReactNode }) {
    return (
        <li className="content-step">
            <p className="content-step-title">{title}</p>
            <div className="content-step-body">{children}</div>
        </li>
    );
}

export function CardGroup({ cols = 2, children }: { cols?: 2 | 3; children: ReactNode }) {
    return <div className={`content-card-group content-card-group-${cols}`}>{children}</div>;
}

export function Card({
    title,
    href,
    children,
}: {
    title: string;
    href?: string;
    children?: ReactNode;
}) {
    const body = (
        <>
            <span className="content-card-title">
                {title}
                {href ? <ArrowRight className="content-card-arrow" aria-hidden /> : null}
            </span>
            {children ? <span className="content-card-body">{children}</span> : null}
        </>
    );

    if (!href) return <div className="content-card">{body}</div>;
    return isExternal(href) ? (
        <a className="content-card" href={href} rel="noreferrer" target="_blank">
            {body}
        </a>
    ) : (
        <Link className="content-card" href={href}>
            {body}
        </Link>
    );
}

export function ParamField({
    name,
    type,
    required = false,
    children,
}: {
    name: string;
    type?: string;
    required?: boolean;
    children: ReactNode;
}) {
    return (
        <div className="content-param">
            <p className="content-param-head">
                <code className="content-param-name">{name}</code>
                {type ? <span className="content-param-type">{type}</span> : null}
                {required ? <span className="content-param-required">required</span> : null}
            </p>
            <div className="content-param-body">{children}</div>
        </div>
    );
}

/**
 * `<details>` rather than a state hook — an accordion that only opens with
 * JavaScript hides its contents from a crawler, which is the opposite of what
 * this pipeline is for.
 */
export function Accordion({ title, children }: { title: string; children: ReactNode }) {
    return (
        <details className="content-accordion">
            <summary className="content-accordion-summary">{title}</summary>
            <div className="content-accordion-body">{children}</div>
        </details>
    );
}

function isExternal(href: string): boolean {
    return /^https?:\/\//i.test(href);
}

/**
 * Markdown's own elements. Anything not overridden renders as the plain tag and
 * is styled by the prose layer.
 */
function ContentLink({ href = '', children }: { href?: string; children?: ReactNode }) {
    if (isExternal(href)) {
        return (
            <a href={href} rel="noreferrer" target="_blank">
                {children}
            </a>
        );
    }
    return <Link href={href}>{children}</Link>;
}

/**
 * A wide table must scroll inside its own frame. Without this the table sets
 * the width of the article and the entire page scrolls sideways on a phone.
 */
function ContentTable({ children }: { children?: ReactNode }) {
    return (
        <div className="content-table-frame">
            <table>{children}</table>
        </div>
    );
}

export const contentComponents = {
    a: ContentLink,
    table: ContentTable,
    Callout,
    Steps,
    Step,
    Card,
    CardGroup,
    ParamField,
    Accordion,
};
